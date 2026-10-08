#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UnrealLinkLibrary.generated.h"

class UAnimSequence;
class UPhysicsAsset;
class USkeletalMesh;
class USkeleton;

/**
 * The C++ half of the Unreal side, called from Content/Python/unreal_link (design §3).
 * Structured data crosses the boundary as JSON strings to keep the Python glue simple.
 */
UCLASS()
class UNREALLINKEDITOR_API UUnrealLinkLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	static const FName TagGuid;
	static const FName TagRole;
	static const FName TagRev;

	/** {guid: object path} for every asset whose UnrealLink.AssetGuid tag is in Guids. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString ResolveGuids(const TArray<FString>& Guids);

	/** Object paths of every asset tagged with this role (e.g. "skeleton"). */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static TArray<FString> FindByRole(const FString& Role);

	/** Import (or reimport onto ReimportTarget) a skeletal mesh FBX through UUnrealLinkPipeline.
	 *  Returns the imported object paths as a JSON list. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString ImportSkeletalMesh(const FString& FbxPath, const FString& DestFolder, const FString& MeshName,
		const FString& SkeletonName, USkeleton* ExistingSkeleton, UPhysicsAsset* ExistingPhysicsAsset,
		bool bCreatePhysicsAsset, USkeletalMesh* ReimportTarget);

	/** Import (or reimport onto ReimportTarget) one animation FBX against Skeleton. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString ImportAnimation(const FString& FbxPath, const FString& DestFolder, const FString& AnimName,
		USkeleton* Skeleton, UAnimSequence* ReimportTarget, float SampleRate);

	/** Apply ordered rename steps (already batched for swaps/chains) to the skeleton, sockets,
	 *  physics asset and animation tracks. Returns a JSON report. (design §9.2 steps 1–5) */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString RenameBones(USkeleton* Skeleton, const TArray<FString>& From, const TArray<FString>& To,
		const TArray<USkeletalMesh*>& Meshes, UPhysicsAsset* PhysicsAsset, const TArray<UAnimSequence*>& Anims);

	/** Skeleton bones as JSON: [{name, parent}] */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString GetSkeletonBones(USkeleton* Skeleton);

	/** Everything verification needs about a mesh as JSON: bones, root transform, bounds,
	 *  materials, sockets (mesh-only), skeleton and physics asset. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString GetMeshInfo(USkeletalMesh* Mesh);

	/** Skeleton sockets as JSON: [{name, bone, location, rotation[pitch,yaw,roll], scale}] */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString GetSkeletonSockets(USkeleton* Skeleton);

	/** Add or update a socket on the skeleton. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static bool SetSkeletonSocket(USkeleton* Skeleton, const FString& Name, const FString& Bone,
		FVector Location, FRotator Rotation, FVector Scale);

	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static bool RenameSkeletonSocket(USkeleton* Skeleton, const FString& From, const FString& To);

	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static bool RemoveSkeletonSocket(USkeleton* Skeleton, const FString& Name);

	/** Re-add mesh-only sockets from a GetMeshInfo() snapshot that a reimport dropped. Returns how many. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static int32 RestoreMeshSockets(USkeletalMesh* Mesh, const FString& SnapshotJson);

	/** Physics bodies and constraints as JSON: {bodies: [bone], constraints: [[bone1, bone2]]} */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString GetPhysicsInfo(UPhysicsAsset* PhysicsAsset);

	/** Animation info as JSON: {skeleton, tracks, num_frames, frame_rate, play_length, root_motion, curves} */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString GetAnimInfo(UAnimSequence* Anim);

	/** Absolute .uasset path for a long package name. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static FString PackageFilename(const FString& PackageName);

	/** Close editors, unload the packages, copy the backup files over them and load them again. */
	UFUNCTION(BlueprintCallable, Category = "Unreal Link")
	static bool RestorePackages(const TArray<FString>& PackageNames, const TArray<FString>& BackupFiles, FString& OutError);

	/** Replace the generic assets pipeline in the project's Interchange stacks with the Unreal Link
	 *  pipeline so drag-and-drop imports of managed rigs go through it. */
	static void InstallDefaultPipeline();
};
