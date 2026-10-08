#include "UnrealLinkLibrary.h"

#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "BoneRenameOps.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/SkeletalMeshSocket.h"
#include "HAL/FileManager.h"
#include "InterchangeManager.h"
#include "InterchangeProjectSettings.h"
#include "Misc/PackageName.h"
#include "PackageTools.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/SavePackage.h"
#include "UnrealLinkPipeline.h"
#include "Editor.h"

DEFINE_LOG_CATEGORY_STATIC(LogUnrealLinkLib, Log, All);

const FName UUnrealLinkLibrary::TagGuid(TEXT("UnrealLink.AssetGuid"));
const FName UUnrealLinkLibrary::TagRole(TEXT("UnrealLink.Role"));
const FName UUnrealLinkLibrary::TagRev(TEXT("UnrealLink.ManifestRev"));

namespace
{
	FString ToJson(const TSharedRef<FJsonObject>& Obj)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out;
	}

	FString ToJson(const TArray<TSharedPtr<FJsonValue>>& Arr)
	{
		FString Out;
		TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Arr, Writer);
		return Out;
	}

	TArray<TSharedPtr<FJsonValue>> Vec(const FVector& V)
	{
		return { MakeShared<FJsonValueNumber>(V.X), MakeShared<FJsonValueNumber>(V.Y), MakeShared<FJsonValueNumber>(V.Z) };
	}

	TArray<TSharedPtr<FJsonValue>> Rot(const FRotator& R)
	{
		return { MakeShared<FJsonValueNumber>(R.Pitch), MakeShared<FJsonValueNumber>(R.Yaw), MakeShared<FJsonValueNumber>(R.Roll) };
	}

	TSharedPtr<FJsonValue> SocketJson(const USkeletalMeshSocket* S)
	{
		TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
		O->SetStringField(TEXT("name"), S->SocketName.ToString());
		O->SetStringField(TEXT("bone"), S->BoneName.ToString());
		O->SetArrayField(TEXT("location"), Vec(S->RelativeLocation));
		O->SetArrayField(TEXT("rotation"), Rot(S->RelativeRotation));
		O->SetArrayField(TEXT("scale"), Vec(S->RelativeScale));
		return MakeShared<FJsonValueObject>(O);
	}

	TArray<TSharedPtr<FJsonValue>> BonesJson(const FReferenceSkeleton& Ref)
	{
		TArray<TSharedPtr<FJsonValue>> Bones;
		for (int32 i = 0; i < Ref.GetRawBoneNum(); ++i)
		{
			TSharedRef<FJsonObject> B = MakeShared<FJsonObject>();
			B->SetStringField(TEXT("name"), Ref.GetBoneName(i).ToString());
			const int32 Parent = Ref.GetParentIndex(i);
			if (Parent == INDEX_NONE)
			{
				B->SetField(TEXT("parent"), MakeShared<FJsonValueNull>());
			}
			else
			{
				B->SetStringField(TEXT("parent"), Ref.GetBoneName(Parent).ToString());
			}
			Bones.Add(MakeShared<FJsonValueObject>(B));
		}
		return Bones;
	}

	TArray<UObject*> RunImport(const FString& FbxPath, const FString& DestFolder, UUnrealLinkPipeline* Pipeline, UObject* ReimportTarget)
	{
		UInterchangeManager& Manager = UInterchangeManager::GetInterchangeManager();
		UInterchangeSourceData* Source = UInterchangeManager::CreateSourceData(FbxPath);

		FImportAssetParameters Params;
		Params.bIsAutomated = true;
		Params.ReimportAsset = ReimportTarget;
		Params.ReimportSourceIndex = INDEX_NONE;
		Params.OverridePipelines.Add(FSoftObjectPath(Pipeline));

		UE::Interchange::FAssetImportResultRef Result = Manager.ImportAssetAsync(DestFolder, Source, Params);
		Result->WaitUntilDone();
		return Result->GetImportedObjects();
	}

	FString ObjectPathsJson(const TArray<UObject*>& Objects)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		for (const UObject* Obj : Objects)
		{
			if (Obj)
			{
				Out.Add(MakeShared<FJsonValueString>(Obj->GetPathName()));
			}
		}
		return ToJson(Out);
	}
}

FString UUnrealLinkLibrary::ResolveGuids(const TArray<FString>& Guids)
{
	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
	TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
	for (const FString& Guid : Guids)
	{
		FARFilter Filter;
		Filter.TagsAndValues.Add(TagGuid, Guid);
		TArray<FAssetData> Found;
		Registry.GetAssets(Filter, Found);
		if (Found.Num() > 0)
		{
			Out->SetStringField(Guid, Found[0].GetObjectPathString());
		}
		if (Found.Num() > 1)
		{
			UE_LOG(LogUnrealLinkLib, Warning, TEXT("Unreal Link: %d assets share guid %s"), Found.Num(), *Guid);
		}
	}
	return ToJson(Out);
}

TArray<FString> UUnrealLinkLibrary::FindByRole(const FString& Role)
{
	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
	FARFilter Filter;
	Filter.TagsAndValues.Add(TagRole, Role);
	TArray<FAssetData> Found;
	Registry.GetAssets(Filter, Found);
	TArray<FString> Out;
	for (const FAssetData& Data : Found)
	{
		Out.Add(Data.GetObjectPathString());
	}
	return Out;
}

FString UUnrealLinkLibrary::ImportSkeletalMesh(const FString& FbxPath, const FString& DestFolder, const FString& MeshName,
	const FString& SkeletonName, USkeleton* ExistingSkeleton, UPhysicsAsset* ExistingPhysicsAsset,
	bool bCreatePhysicsAsset, USkeletalMesh* ReimportTarget)
{
	UUnrealLinkPipeline* Pipeline = NewObject<UUnrealLinkPipeline>(GetTransientPackage());
	Pipeline->bJobContext = true;
	Pipeline->MeshName = MeshName;
	Pipeline->SkeletonName = SkeletonName;
	Pipeline->ExistingSkeleton = ExistingSkeleton;
	Pipeline->ExistingPhysicsAsset = ExistingPhysicsAsset;
	Pipeline->bCreatePhysicsAsset = bCreatePhysicsAsset;
	return ObjectPathsJson(RunImport(FbxPath, DestFolder, Pipeline, ReimportTarget));
}

FString UUnrealLinkLibrary::ImportAnimation(const FString& FbxPath, const FString& DestFolder, const FString& AnimName,
	USkeleton* Skeleton, UAnimSequence* ReimportTarget, float SampleRate)
{
	UUnrealLinkPipeline* Pipeline = NewObject<UUnrealLinkPipeline>(GetTransientPackage());
	Pipeline->bJobContext = true;
	Pipeline->bAnimationOnly = true;
	Pipeline->AnimName = AnimName;
	Pipeline->ExistingSkeleton = Skeleton;
	Pipeline->SampleRate = SampleRate;
	return ObjectPathsJson(RunImport(FbxPath, DestFolder, Pipeline, ReimportTarget));
}

FString UUnrealLinkLibrary::RenameBones(USkeleton* Skeleton, const TArray<FString>& From, const TArray<FString>& To,
	const TArray<USkeletalMesh*>& Meshes, UPhysicsAsset* PhysicsAsset, const TArray<UAnimSequence*>& Anims)
{
	if (!Skeleton || From.Num() != To.Num())
	{
		return TEXT("{\"errors\": [\"bad arguments\"]}");
	}
	TArray<TPair<FName, FName>> Steps;
	for (int32 i = 0; i < From.Num(); ++i)
	{
		Steps.Emplace(FName(*From[i]), FName(*To[i]));
	}
	return ToJson(FBoneRenameOps::Apply(Skeleton, Steps, Meshes, PhysicsAsset, Anims));
}

FString UUnrealLinkLibrary::GetSkeletonBones(USkeleton* Skeleton)
{
	return Skeleton ? ToJson(BonesJson(Skeleton->GetReferenceSkeleton())) : TEXT("[]");
}

FString UUnrealLinkLibrary::GetMeshInfo(USkeletalMesh* Mesh)
{
	TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
	if (!Mesh)
	{
		return ToJson(O);
	}
	const FReferenceSkeleton& Ref = Mesh->GetRefSkeleton();
	O->SetArrayField(TEXT("bones"), BonesJson(Ref));
	if (Ref.GetRawBoneNum() > 0)
	{
		const FTransform& Root = Ref.GetRawRefBonePose()[0];
		O->SetArrayField(TEXT("root_scale"), Vec(Root.GetScale3D()));
		O->SetArrayField(TEXT("root_location"), Vec(Root.GetLocation()));
		O->SetArrayField(TEXT("root_rotation"), Rot(Root.Rotator()));
	}
	const FBoxSphereBounds Bounds = Mesh->GetImportedBounds();
	O->SetArrayField(TEXT("bounds_size"), Vec(Bounds.BoxExtent * 2.0));
	O->SetStringField(TEXT("skeleton"), Mesh->GetSkeleton() ? Mesh->GetSkeleton()->GetPathName() : FString());
	O->SetStringField(TEXT("physics_asset"), Mesh->GetPhysicsAsset() ? Mesh->GetPhysicsAsset()->GetPathName() : FString());

	TArray<TSharedPtr<FJsonValue>> Materials;
	for (const FSkeletalMaterial& M : Mesh->GetMaterials())
	{
		TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
		S->SetStringField(TEXT("slot"), M.MaterialSlotName.ToString());
		S->SetStringField(TEXT("material"), M.MaterialInterface ? M.MaterialInterface->GetPathName() : FString());
		Materials.Add(MakeShared<FJsonValueObject>(S));
	}
	O->SetArrayField(TEXT("materials"), Materials);

	TArray<TSharedPtr<FJsonValue>> Sockets;
	for (const USkeletalMeshSocket* S : Mesh->GetMeshOnlySocketList())
	{
		if (S)
		{
			Sockets.Add(SocketJson(S));
		}
	}
	O->SetArrayField(TEXT("mesh_sockets"), Sockets);
	return ToJson(O);
}

FString UUnrealLinkLibrary::GetSkeletonSockets(USkeleton* Skeleton)
{
	TArray<TSharedPtr<FJsonValue>> Sockets;
	if (Skeleton)
	{
		for (const USkeletalMeshSocket* S : Skeleton->Sockets)
		{
			if (S)
			{
				Sockets.Add(SocketJson(S));
			}
		}
	}
	return ToJson(Sockets);
}

bool UUnrealLinkLibrary::SetSkeletonSocket(USkeleton* Skeleton, const FString& Name, const FString& Bone,
	FVector Location, FRotator Rotation, FVector Scale)
{
	if (!Skeleton || Skeleton->GetReferenceSkeleton().FindBoneIndex(FName(*Bone)) == INDEX_NONE)
	{
		return false;
	}
	Skeleton->Modify();
	USkeletalMeshSocket* Socket = nullptr;
	for (USkeletalMeshSocket* S : Skeleton->Sockets)
	{
		if (S && S->SocketName == FName(*Name))
		{
			Socket = S;
			break;
		}
	}
	if (!Socket)
	{
		Socket = NewObject<USkeletalMeshSocket>(Skeleton);
		Socket->SocketName = FName(*Name);
		Skeleton->Sockets.Add(Socket);
	}
	Socket->Modify();
	Socket->BoneName = FName(*Bone);
	Socket->RelativeLocation = Location;
	Socket->RelativeRotation = Rotation;
	Socket->RelativeScale = Scale;
	Skeleton->MarkPackageDirty();
	return true;
}

bool UUnrealLinkLibrary::RenameSkeletonSocket(USkeleton* Skeleton, const FString& From, const FString& To)
{
	if (!Skeleton)
	{
		return false;
	}
	for (USkeletalMeshSocket* S : Skeleton->Sockets)
	{
		if (S && S->SocketName == FName(*From))
		{
			Skeleton->Modify();
			S->Modify();
			S->SocketName = FName(*To);
			Skeleton->MarkPackageDirty();
			return true;
		}
	}
	return false;
}

bool UUnrealLinkLibrary::RemoveSkeletonSocket(USkeleton* Skeleton, const FString& Name)
{
	if (!Skeleton)
	{
		return false;
	}
	Skeleton->Modify();
	const int32 Removed = Skeleton->Sockets.RemoveAll([&Name](const TObjectPtr<USkeletalMeshSocket>& S)
	{
		return S && S->SocketName == FName(*Name);
	});
	if (Removed)
	{
		Skeleton->MarkPackageDirty();
	}
	return Removed > 0;
}

int32 UUnrealLinkLibrary::RestoreMeshSockets(USkeletalMesh* Mesh, const FString& SnapshotJson)
{
	TSharedPtr<FJsonObject> Snap;
	if (!Mesh || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SnapshotJson), Snap) || !Snap.IsValid())
	{
		return 0;
	}
	const TArray<TSharedPtr<FJsonValue>>* Sockets = nullptr;
	if (!Snap->TryGetArrayField(TEXT("mesh_sockets"), Sockets))
	{
		return 0;
	}
	auto ReadVec = [](const TSharedPtr<FJsonObject>& O, const TCHAR* Field)
	{
		const TArray<TSharedPtr<FJsonValue>>& A = O->GetArrayField(Field);
		return FVector(A[0]->AsNumber(), A[1]->AsNumber(), A[2]->AsNumber());
	};
	int32 Restored = 0;
	for (const TSharedPtr<FJsonValue>& V : *Sockets)
	{
		const TSharedPtr<FJsonObject> O = V->AsObject();
		const FName Name(*O->GetStringField(TEXT("name")));
		if (Mesh->FindSocket(Name))
		{
			continue;
		}
		USkeletalMeshSocket* Socket = NewObject<USkeletalMeshSocket>(Mesh);
		Socket->SocketName = Name;
		Socket->BoneName = FName(*O->GetStringField(TEXT("bone")));
		Socket->RelativeLocation = ReadVec(O, TEXT("location"));
		const FVector R = ReadVec(O, TEXT("rotation"));
		Socket->RelativeRotation = FRotator(R.X, R.Y, R.Z);
		Socket->RelativeScale = ReadVec(O, TEXT("scale"));
		Mesh->AddSocket(Socket);
		++Restored;
	}
	if (Restored)
	{
		Mesh->MarkPackageDirty();
	}
	return Restored;
}

FString UUnrealLinkLibrary::GetPhysicsInfo(UPhysicsAsset* PhysicsAsset)
{
	TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> Bodies, Constraints;
	if (PhysicsAsset)
	{
		for (const USkeletalBodySetup* B : PhysicsAsset->SkeletalBodySetups)
		{
			if (B)
			{
				Bodies.Add(MakeShared<FJsonValueString>(B->BoneName.ToString()));
			}
		}
		for (const UPhysicsConstraintTemplate* C : PhysicsAsset->ConstraintSetup)
		{
			if (C)
			{
				Constraints.Add(MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{
					MakeShared<FJsonValueString>(C->DefaultInstance.ConstraintBone1.ToString()),
					MakeShared<FJsonValueString>(C->DefaultInstance.ConstraintBone2.ToString()) }));
			}
		}
	}
	O->SetArrayField(TEXT("bodies"), Bodies);
	O->SetArrayField(TEXT("constraints"), Constraints);
	return ToJson(O);
}

FString UUnrealLinkLibrary::GetAnimInfo(UAnimSequence* Anim)
{
	TSharedRef<FJsonObject> O = MakeShared<FJsonObject>();
	if (!Anim || !Anim->GetDataModel())
	{
		return ToJson(O);
	}
	const IAnimationDataModel* Model = Anim->GetDataModel();
	TArray<FName> TrackNames;
	Model->GetBoneTrackNames(TrackNames);
	TArray<TSharedPtr<FJsonValue>> Tracks;
	for (const FName& N : TrackNames)
	{
		Tracks.Add(MakeShared<FJsonValueString>(N.ToString()));
	}
	TArray<TSharedPtr<FJsonValue>> Curves;
	for (const FFloatCurve& Curve : Model->GetFloatCurves())
	{
		Curves.Add(MakeShared<FJsonValueString>(Curve.GetName().ToString()));
	}
	O->SetStringField(TEXT("skeleton"), Anim->GetSkeleton() ? Anim->GetSkeleton()->GetPathName() : FString());
	O->SetArrayField(TEXT("tracks"), Tracks);
	O->SetArrayField(TEXT("curves"), Curves);
	O->SetNumberField(TEXT("num_frames"), Model->GetNumberOfFrames());
	O->SetNumberField(TEXT("frame_rate"), Model->GetFrameRate().AsDecimal());
	O->SetNumberField(TEXT("play_length"), Model->GetPlayLength());
	O->SetBoolField(TEXT("root_motion"), Anim->bEnableRootMotion);
	return ToJson(O);
}

FString UUnrealLinkLibrary::PackageFilename(const FString& PackageName)
{
	FString Filename;
	if (!FPackageName::TryConvertLongPackageNameToFilename(PackageName, Filename, FPackageName::GetAssetPackageExtension()))
	{
		return FString();
	}
	return FPaths::ConvertRelativePathToFull(Filename);
}

bool UUnrealLinkLibrary::RestorePackages(const TArray<FString>& PackageNames, const TArray<FString>& BackupFiles, FString& OutError)
{
	if (PackageNames.Num() != BackupFiles.Num())
	{
		OutError = TEXT("package and backup lists differ in length");
		return false;
	}
	UAssetEditorSubsystem* Editors = GEditor ? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>() : nullptr;
	TArray<UPackage*> Loaded;
	for (const FString& Name : PackageNames)
	{
		if (UPackage* Pkg = FindPackage(nullptr, *Name))
		{
			if (Editors)
			{
				ForEachObjectWithPackage(Pkg, [Editors](UObject* Obj)
				{
					Editors->CloseAllEditorsForAsset(Obj);
					return true;
				}, EGetObjectsFlags::None);
			}
			Loaded.Add(Pkg);
		}
	}
	if (Loaded.Num() > 0)
	{
		FText Error;
		if (!UPackageTools::UnloadPackages(Loaded, Error, /*bUnloadDirtyPackages=*/true))
		{
			OutError = Error.ToString();
			return false;
		}
	}
	for (int32 i = 0; i < PackageNames.Num(); ++i)
	{
		const FString Dest = PackageFilename(PackageNames[i]);
		if (IFileManager::Get().Copy(*Dest, *BackupFiles[i], /*bReplace=*/true) != COPY_OK)
		{
			OutError = FString::Printf(TEXT("could not copy %s over %s"), *BackupFiles[i], *Dest);
			return false;
		}
	}
	for (const FString& Name : PackageNames)
	{
		LoadPackage(nullptr, *Name, LOAD_None);
	}
	return true;
}

void UUnrealLinkLibrary::InstallDefaultPipeline()
{
	static const TCHAR* PackagePath = TEXT("/Game/UnrealLink/UL_AssetsPipeline");
	static const TCHAR* ObjectPath = TEXT("/Game/UnrealLink/UL_AssetsPipeline.UL_AssetsPipeline");

	UUnrealLinkPipeline* Asset = LoadObject<UUnrealLinkPipeline>(nullptr, ObjectPath, nullptr, LOAD_NoWarn | LOAD_Quiet);
	if (!Asset)
	{
		UPackage* Package = CreatePackage(PackagePath);
		Asset = NewObject<UUnrealLinkPipeline>(Package, TEXT("UL_AssetsPipeline"), RF_Public | RF_Standalone);
		FAssetRegistryModule::AssetCreated(Asset);
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		UPackage::SavePackage(Package, Asset, *PackageFilename(PackagePath), Args);
	}

	const FSoftObjectPath Generic(TEXT("/Interchange/Pipelines/DefaultAssetsPipeline.DefaultAssetsPipeline"));
	UInterchangeProjectSettings* Settings = GetMutableDefault<UInterchangeProjectSettings>();
	int32 Replaced = 0;
	for (TPair<FName, FInterchangePipelineStack>& Stack : Settings->ContentImportSettings.PipelineStacks)
	{
		for (FSoftObjectPath& Path : Stack.Value.Pipelines)
		{
			if (Path == Generic)
			{
				Path = FSoftObjectPath(Asset);
				++Replaced;
			}
		}
	}
	UE_LOG(LogUnrealLinkLib, Log, TEXT("Unreal Link: pipeline installed in %d Interchange stack(s)"), Replaced);
}
