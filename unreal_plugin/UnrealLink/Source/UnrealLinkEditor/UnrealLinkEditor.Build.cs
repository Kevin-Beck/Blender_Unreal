using UnrealBuildTool;

public class UnrealLinkEditor : ModuleRules
{
	public UnrealLinkEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InterchangeCore",
			"InterchangeEngine",
			"InterchangePipelines",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AssetRegistry",
			"AssetTools",
			"InterchangeNodes",
			"InterchangeFactoryNodes",
			"Json",
			"PhysicsCore",
			"UnrealEd",
		});
	}
}
