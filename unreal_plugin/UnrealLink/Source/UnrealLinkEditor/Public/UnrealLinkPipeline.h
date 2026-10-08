#pragma once

#include "CoreMinimal.h"
#include "InterchangeGenericAssetsPipeline.h"
#include "UnrealLinkPipeline.generated.h"

class USkeleton;
class UPhysicsAsset;

/**
 * The generic assets pipeline with the Unreal Link profile forced on top (design §6.2).
 *
 * With job context (set by UUnrealLinkLibrary before an import) it applies the job's names, the
 * GUID-resolved skeleton, and the physics rules. Without job context (drag-and-drop, or a manual
 * reimport) it looks for a managed skeleton the file matches and, if it finds one, forces it and
 * turns off physics-asset and material creation so no duplicates appear.
 */
UCLASS(BlueprintType, EditInlineNew)
class UNREALLINKEDITOR_API UUnrealLinkPipeline : public UInterchangeGenericAssetsPipeline
{
	GENERATED_BODY()

public:
	UUnrealLinkPipeline();

	/** Applies the profile values that are the same for every import. */
	void ApplyProfile();

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	bool bJobContext = false;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	bool bAnimationOnly = false;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	FString MeshName;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	FString SkeletonName;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	FString AnimName;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	TSoftObjectPtr<USkeleton> ExistingSkeleton;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	TSoftObjectPtr<UPhysicsAsset> ExistingPhysicsAsset;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	bool bCreatePhysicsAsset = false;

	UPROPERTY(EditAnywhere, Category = "Unreal Link")
	float SampleRate = 30.f;

protected:
	virtual void ExecutePipeline(UInterchangeBaseNodeContainer* BaseNodeContainer,
		const TArray<UInterchangeSourceData*>& SourceDatas, const FString& ContentBasePath) override;

private:
	void ConfigureFromJob();
	bool ConfigureFromManagedSkeleton(UInterchangeBaseNodeContainer* BaseNodeContainer);
	void PostProcessFactoryNodes(UInterchangeBaseNodeContainer* BaseNodeContainer);
};
