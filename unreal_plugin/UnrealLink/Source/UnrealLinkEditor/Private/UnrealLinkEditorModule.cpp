#include "Modules/ModuleManager.h"
#include "UnrealLinkLibrary.h"

class FUnrealLinkEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		// Make the GUID/role/rev metadata tags searchable in the asset registry, so assets are
		// found by GUID even after they're moved or renamed in the Content Browser (design §4.4).
		TSet<FName>& Tags = UObject::GetMetaDataTagsForAssetRegistry();
		Tags.Add(UUnrealLinkLibrary::TagGuid);
		Tags.Add(UUnrealLinkLibrary::TagRole);
		Tags.Add(UUnrealLinkLibrary::TagRev);

		// The module loads at PostEngineInit, so the engine is ready here.
		UUnrealLinkLibrary::InstallDefaultPipeline();
	}
};

IMPLEMENT_MODULE(FUnrealLinkEditorModule, UnrealLinkEditor)
