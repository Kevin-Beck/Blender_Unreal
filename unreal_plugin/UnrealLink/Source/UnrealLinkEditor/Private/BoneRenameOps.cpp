#include "BoneRenameOps.h"

#include "Animation/AnimData/IAnimationDataController.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/SkeletalMeshSocket.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "ReferenceSkeleton.h"

#define LOCTEXT_NAMESPACE "UnrealLinkBoneRename"

TMap<FName, FName> FBoneRenameOps::Composite(const TArray<TPair<FName, FName>>& Steps)
{
	// Track where each original name ends up.
	TMap<FName, FName> Current;
	for (const TPair<FName, FName>& Step : Steps)
	{
		bool bFound = false;
		for (TPair<FName, FName>& KV : Current)
		{
			if (KV.Value == Step.Key)
			{
				KV.Value = Step.Value;
				bFound = true;
				break;
			}
		}
		if (!bFound)
		{
			Current.Add(Step.Key, Step.Value);
		}
	}
	TMap<FName, FName> Out;
	for (const TPair<FName, FName>& KV : Current)
	{
		if (KV.Key != KV.Value)
		{
			Out.Add(KV.Key, KV.Value);
		}
	}
	return Out;
}

int32 FBoneRenameOps::RenameAnimTracks(UAnimSequence* Anim, const TMap<FName, FName>& Map)
{
	const IAnimationDataModel* Model = Anim->GetDataModel();
	if (!Model)
	{
		return 0;
	}

	struct FTrackCopy
	{
		FName To;
		TArray<FVector3f> Pos;
		TArray<FQuat4f> Rot;
		TArray<FVector3f> Scale;
	};
	TArray<FTrackCopy> Copies;
	TArray<FName> ToRemove;
	for (const TPair<FName, FName>& KV : Map)
	{
		if (!Model->IsValidBoneTrackName(KV.Key))
		{
			continue;
		}
		TArray<FTransform> Keys;
		Model->GetBoneTrackTransforms(KV.Key, Keys);
		FTrackCopy& Copy = Copies.AddDefaulted_GetRef();
		Copy.To = KV.Value;
		for (const FTransform& T : Keys)
		{
			Copy.Pos.Add(FVector3f(T.GetLocation()));
			Copy.Rot.Add(FQuat4f(T.GetRotation()));
			Copy.Scale.Add(FVector3f(T.GetScale3D()));
		}
		ToRemove.Add(KV.Key);
	}
	if (Copies.Num() == 0)
	{
		return 0;
	}

	Anim->Modify();
	IAnimationDataController& Controller = Anim->GetController();
	IAnimationDataController::FScopedBracket Bracket(Controller, LOCTEXT("RenameTracks", "Unreal Link: rename bone tracks"));
	// In 5.x the data model keeps its own rig hierarchy built from the skeleton; resync it with the
	// renamed skeleton first. This drops tracks whose bones no longer exist, which is why the keys
	// were copied out above.
	Controller.UpdateWithSkeleton(Anim->GetSkeleton());
	// Remove what's left of the old tracks (e.g. both sides of a swap) so re-adding never collides.
	for (const FName& Name : ToRemove)
	{
		if (Model->IsValidBoneTrackName(Name))
		{
			Controller.RemoveBoneTrack(Name);
		}
	}
	for (const FTrackCopy& Copy : Copies)
	{
		Controller.AddBoneCurve(Copy.To);
		Controller.SetBoneTrackKeys(Copy.To, Copy.Pos, Copy.Rot, Copy.Scale);
	}
	Anim->MarkPackageDirty();
	return Copies.Num();
}

TSharedRef<FJsonObject> FBoneRenameOps::Apply(USkeleton* Skeleton, const TArray<TPair<FName, FName>>& Steps,
	const TArray<USkeletalMesh*>& Meshes, UPhysicsAsset* PhysicsAsset, const TArray<UAnimSequence*>& Anims)
{
	TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> Errors;
	TArray<TSharedPtr<FJsonValue>> SkeletonSteps;

	// 1. Skeleton, step by step. A step whose source is gone but whose target exists was already
	//    applied (a retried job), so it's skipped rather than failed.
	Skeleton->Modify();
	for (const TPair<FName, FName>& Step : Steps)
	{
		const FReferenceSkeleton& Ref = Skeleton->GetReferenceSkeleton();
		FString State;
		if (Ref.FindBoneIndex(Step.Key) == INDEX_NONE)
		{
			State = Ref.FindBoneIndex(Step.Value) != INDEX_NONE ? TEXT("already applied") : TEXT("missing");
			if (State == TEXT("missing"))
			{
				Errors.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("skeleton has no bone '%s'"), *Step.Key.ToString())));
			}
		}
		else if (Ref.FindBoneIndex(Step.Value) != INDEX_NONE)
		{
			State = TEXT("target exists");
			Errors.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("skeleton already has a bone '%s'"), *Step.Value.ToString())));
		}
		else
		{
			FReferenceSkeletonModifier Modifier(Skeleton);
			Modifier.Rename(Step.Key, Step.Value);
			State = TEXT("renamed");
		}
		TSharedRef<FJsonObject> S = MakeShared<FJsonObject>();
		S->SetStringField(TEXT("from"), Step.Key.ToString());
		S->SetStringField(TEXT("to"), Step.Value.ToString());
		S->SetStringField(TEXT("skeleton"), State);
		SkeletonSteps.Add(MakeShared<FJsonValueObject>(S));
	}
	// Rename keeps bone indices, so the bone tree is untouched; only name-keyed caches go stale.
	Skeleton->ClearCacheData();
	Skeleton->MarkPackageDirty();
	Report->SetArrayField(TEXT("steps"), SkeletonSteps);

	const TMap<FName, FName> Map = Composite(Steps);

	// 3. Sockets on the skeleton and on each mesh.
	int32 Sockets = 0;
	auto RenameSocketBones = [&Map, &Sockets](TArray<TObjectPtr<USkeletalMeshSocket>>& List)
	{
		for (USkeletalMeshSocket* Socket : List)
		{
			if (const FName* To = Socket ? Map.Find(Socket->BoneName) : nullptr)
			{
				Socket->Modify();
				Socket->BoneName = *To;
				++Sockets;
			}
		}
	};
	RenameSocketBones(Skeleton->Sockets);
	for (USkeletalMesh* Mesh : Meshes)
	{
		if (!Mesh)
		{
			continue;
		}
		Mesh->Modify();
		for (USkeletalMeshSocket* Socket : Mesh->GetMeshOnlySocketList())
		{
			if (const FName* To = Socket ? Map.Find(Socket->BoneName) : nullptr)
			{
				Socket->BoneName = *To;
				++Sockets;
			}
		}
		Mesh->MarkPackageDirty();
	}
	Report->SetNumberField(TEXT("sockets"), Sockets);

	// 4. Physics bodies and constraints.
	int32 Bodies = 0, Constraints = 0;
	if (PhysicsAsset)
	{
		PhysicsAsset->Modify();
		for (USkeletalBodySetup* Body : PhysicsAsset->SkeletalBodySetups)
		{
			if (const FName* To = Body ? Map.Find(Body->BoneName) : nullptr)
			{
				Body->Modify();
				Body->BoneName = *To;
				++Bodies;
			}
		}
		for (UPhysicsConstraintTemplate* Template : PhysicsAsset->ConstraintSetup)
		{
			if (!Template)
			{
				continue;
			}
			FConstraintInstance& C = Template->DefaultInstance;
			bool bChanged = false;
			for (FName* Name : { &C.ConstraintBone1, &C.ConstraintBone2, &C.JointName })
			{
				if (const FName* To = Map.Find(*Name))
				{
					*Name = *To;
					bChanged = true;
				}
			}
			if (bChanged)
			{
				Template->Modify();
				++Constraints;
			}
		}
		PhysicsAsset->UpdateBodySetupIndexMap();
		PhysicsAsset->UpdateBoundsBodiesArray();
		PhysicsAsset->MarkPackageDirty();
	}
	Report->SetNumberField(TEXT("bodies"), Bodies);
	Report->SetNumberField(TEXT("constraints"), Constraints);

	// 5. Animation tracks (there is no rename API: copy keys to a new track, remove the old one).
	int32 AnimCount = 0, Tracks = 0;
	for (UAnimSequence* Anim : Anims)
	{
		if (!Anim)
		{
			continue;
		}
		const int32 N = RenameAnimTracks(Anim, Map);
		Tracks += N;
		AnimCount += N > 0 ? 1 : 0;
	}
	Report->SetNumberField(TEXT("anims"), AnimCount);
	Report->SetNumberField(TEXT("tracks"), Tracks);
	Report->SetArrayField(TEXT("errors"), Errors);
	return Report;
}

#undef LOCTEXT_NAMESPACE
