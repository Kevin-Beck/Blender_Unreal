#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UAnimSequence;
class UPhysicsAsset;
class USkeletalMesh;
class USkeleton;

/**
 * Bone rename propagation (design §9.2, steps 1–5).
 *
 * The skeleton is renamed step by step, because every intermediate state must have unique names
 * (swaps go through temporary names). Everything else only stores names, so it gets the composite
 * old → new mapping in one pass. The skeletal mesh's own reference skeleton is not renamed here:
 * the job always reimports the mesh right after, and that rebuilds it from the FBX.
 */
struct FBoneRenameOps
{
	static TSharedRef<FJsonObject> Apply(USkeleton* Skeleton, const TArray<TPair<FName, FName>>& Steps,
		const TArray<USkeletalMesh*>& Meshes, UPhysicsAsset* PhysicsAsset, const TArray<UAnimSequence*>& Anims);

	/** Collapse ordered steps into the final old → new mapping (temporary names drop out). */
	static TMap<FName, FName> Composite(const TArray<TPair<FName, FName>>& Steps);

private:
	static int32 RenameAnimTracks(UAnimSequence* Anim, const TMap<FName, FName>& Map);
};
