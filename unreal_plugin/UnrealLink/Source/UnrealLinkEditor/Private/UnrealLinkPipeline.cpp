#include "UnrealLinkPipeline.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Animation/Skeleton.h"
#include "InterchangeAnimSequenceFactoryNode.h"
#include "InterchangeGenericAnimationPipeline.h"
#include "InterchangeGenericMaterialPipeline.h"
#include "InterchangeGenericMeshPipeline.h"
#include "InterchangeGenericTexturePipeline.h"
#include "InterchangeMaterialFactoryNode.h"
#include "InterchangeJointNode.h"
#include "InterchangeSkeletalMeshFactoryNode.h"
#include "InterchangeSkeletonFactoryNode.h"
#include "Nodes/InterchangeBaseNodeContainer.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "UnrealLinkLibrary.h"

DEFINE_LOG_CATEGORY_STATIC(LogUnrealLink, Log, All);

// Defaults stay those of the generic pipeline, so unrelated drag-and-drop imports (static meshes,
// textures, ...) behave exactly as before. The profile is applied only to managed imports.
UUnrealLinkPipeline::UUnrealLinkPipeline() = default;

void UUnrealLinkPipeline::ApplyProfile()
{
	// Values from design §6.2. Convert Scene / Force Front X / Convert Scene Unit are FBX
	// translator settings and stay at their defaults (convert on, front-X off, unit on): confirm in M0.
	ReimportStrategy = EReimportStrategyFlags::ApplyNoProperties;

	if (CommonMeshesProperties)
	{
		CommonMeshesProperties->ForceAllMeshAsType = EInterchangeForceMeshType::IFMT_SkeletalMesh;
		CommonMeshesProperties->bRecomputeNormals = false;
		CommonMeshesProperties->bRecomputeTangents = true;
		CommonMeshesProperties->bUseMikkTSpace = true;
		CommonMeshesProperties->bImportLods = false;
	}
	if (CommonSkeletalMeshesAndAnimationsProperties)
	{
		CommonSkeletalMeshesAndAnimationsProperties->bUseT0AsRefPose = false;
	}
	if (MeshPipeline)
	{
		MeshPipeline->bImportStaticMeshes = false;
		MeshPipeline->bImportSkeletalMeshes = true;
		MeshPipeline->bImportMorphTargets = true;
		// Rest-pose edits in Blender must reach the skeleton, not just the mesh.
		MeshPipeline->bUpdateSkeletonReferencePose = true;
	}
	if (AnimationPipeline)
	{
		AnimationPipeline->bImportAnimations = true;
		AnimationPipeline->bImportBoneTracks = true;
		AnimationPipeline->AnimationRange = EInterchangeAnimationRange::Timeline;
		AnimationPipeline->bUse30HzToBakeBoneAnimation = false;
		AnimationPipeline->bDeleteExistingMorphTargetCurves = false;
		AnimationPipeline->bDoNotImportCurveWithZero = false;
	}
	if (MaterialPipeline)
	{
		// Unreal owns materials; the tool only manages slots.
		MaterialPipeline->bImportMaterials = false;
	}
	if (MaterialPipeline && MaterialPipeline->TexturePipeline)
	{
		MaterialPipeline->TexturePipeline->bImportTextures = false;
	}
}

void UUnrealLinkPipeline::ConfigureFromJob()
{
	ApplyProfile();
	if (CommonSkeletalMeshesAndAnimationsProperties)
	{
		CommonSkeletalMeshesAndAnimationsProperties->Skeleton = ExistingSkeleton;
		CommonSkeletalMeshesAndAnimationsProperties->bImportOnlyAnimations = bAnimationOnly;
	}
	if (MeshPipeline)
	{
		MeshPipeline->bImportSkeletalMeshes = !bAnimationOnly;
		MeshPipeline->bCreatePhysicsAsset = !bAnimationOnly && bCreatePhysicsAsset && ExistingPhysicsAsset.IsNull();
		MeshPipeline->PhysicsAsset = ExistingPhysicsAsset;
	}
	if (AnimationPipeline)
	{
		AnimationPipeline->bImportAnimations = bAnimationOnly;
		AnimationPipeline->CustomBoneAnimationSampleRate = FMath::RoundToInt(SampleRate);
	}
}

bool UUnrealLinkPipeline::ConfigureFromManagedSkeleton(UInterchangeBaseNodeContainer* BaseNodeContainer)
{
	TSet<FString> JointNames;
	FString RootJoint;
	BaseNodeContainer->IterateNodesOfType<UInterchangeJointNode>([&](const FString& NodeUid, UInterchangeJointNode* Node)
	{
		JointNames.Add(Node->GetDisplayLabel());
		if (!Cast<UInterchangeJointNode>(BaseNodeContainer->GetNode(Node->GetParentUid())))
		{
			RootJoint = Node->GetDisplayLabel();
		}
	});
	if (JointNames.Num() == 0)
	{
		return false;
	}

	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry").Get();
	FARFilter Filter;
	Filter.TagsAndValues.Add(UUnrealLinkLibrary::TagRole, FString(TEXT("skeleton")));
	TArray<FAssetData> Found;
	Registry.GetAssets(Filter, Found);

	for (const FAssetData& Data : Found)
	{
		USkeleton* Skeleton = Cast<USkeleton>(Data.GetAsset());
		if (!Skeleton)
		{
			continue;
		}
		const FReferenceSkeleton& Ref = Skeleton->GetReferenceSkeleton();
		if (Ref.GetNum() == 0 || Ref.GetBoneName(0).ToString() != RootJoint)
		{
			continue;
		}
		int32 Matches = 0;
		for (const FString& Name : JointNames)
		{
			Matches += Ref.FindBoneIndex(FName(*Name)) != INDEX_NONE ? 1 : 0;
		}
		if (Matches * 10 < JointNames.Num() * 9)
		{
			continue;
		}
		UE_LOG(LogUnrealLink, Warning,
			TEXT("Unreal Link: this file matches the managed skeleton %s. Forcing it and skipping physics/material creation. "
				 "Send from Blender instead so the manifest history stays in sync."), *Skeleton->GetPathName());
		ApplyProfile();
		CommonSkeletalMeshesAndAnimationsProperties->Skeleton = Skeleton;
		MeshPipeline->bCreatePhysicsAsset = false;
		return true;
	}
	return false;
}

void UUnrealLinkPipeline::PostProcessFactoryNodes(UInterchangeBaseNodeContainer* BaseNodeContainer)
{
	BaseNodeContainer->IterateNodesOfType<UInterchangeSkeletalMeshFactoryNode>([&](const FString& Uid, UInterchangeSkeletalMeshFactoryNode* Node)
	{
		if (!MeshName.IsEmpty())
		{
			Node->SetDisplayLabel(MeshName);
		}
		if (!ExistingSkeleton.IsNull())
		{
			Node->SetCustomSkeletonSoftObjectPath(ExistingSkeleton.ToSoftObjectPath());
		}
		if (!ExistingPhysicsAsset.IsNull())
		{
			Node->SetCustomCreatePhysicsAsset(false);
			Node->SetCustomPhysicAssetSoftObjectPath(ExistingPhysicsAsset.ToSoftObjectPath());
		}
	});
	BaseNodeContainer->IterateNodesOfType<UInterchangeSkeletonFactoryNode>([&](const FString& Uid, UInterchangeSkeletonFactoryNode* Node)
	{
		if (!SkeletonName.IsEmpty() && ExistingSkeleton.IsNull())
		{
			Node->SetDisplayLabel(SkeletonName);
		}
	});
	BaseNodeContainer->IterateNodesOfType<UInterchangeAnimSequenceFactoryNode>([&](const FString& Uid, UInterchangeAnimSequenceFactoryNode* Node)
	{
		if (!AnimName.IsEmpty())
		{
			Node->SetDisplayLabel(AnimName);
		}
		if (!ExistingSkeleton.IsNull())
		{
			Node->SetCustomSkeletonSoftObjectPath(ExistingSkeleton.ToSoftObjectPath());
		}
	});
	// Materials belong to Unreal. Slots still get their names from the FBX material names.
	BaseNodeContainer->IterateNodesOfType<UInterchangeBaseMaterialFactoryNode>([&](const FString& Uid, UInterchangeBaseMaterialFactoryNode* Node)
	{
		Node->SetEnabled(false);
	});
}

void UUnrealLinkPipeline::ExecutePipeline(UInterchangeBaseNodeContainer* BaseNodeContainer,
	const TArray<UInterchangeSourceData*>& SourceDatas, const FString& ContentBasePath)
{
	bool bManaged = bJobContext;
	if (bJobContext)
	{
		ConfigureFromJob();
	}
	else
	{
		bManaged = ConfigureFromManagedSkeleton(BaseNodeContainer);
	}

	Super::ExecutePipeline(BaseNodeContainer, SourceDatas, ContentBasePath);

	if (bManaged)
	{
		PostProcessFactoryNodes(BaseNodeContainer);
	}
}
