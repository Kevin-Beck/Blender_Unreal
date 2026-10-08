# Setting up Unreal Link on Windows

This guide installs Unreal Link on a Windows machine. It has two parts: the **Unreal plugin**, installed into one
Unreal project, and the **Blender add-on**. Both programs run on the same PC.

> **Status:** so far, Unreal Link has only been tested on Linux (Unreal 5.8.3, Blender 5.2.2). The Windows steps below
> follow the same process, but they haven't been run on Windows yet. If something doesn't match, see
> [Troubleshooting](#troubleshooting).

## 1. What you need

| Item | Notes |
|---|---|
| **Unreal Engine 5.x** | Install it from the Epic Games Launcher. Development used 5.8. |
| **Blender 5.x** | Get it from [blender.org](https://www.blender.org/download/). Development used 5.2 LTS. |
| **Visual Studio 2022** (Community is fine) | This compiles the plugin's C++ code. In the Visual Studio Installer, tick **Game development with C++**, and under its optional parts tick the latest **Windows 10/11 SDK** and **Unreal Engine installer** (or **Unreal Engine tools**, depending on version). |
| **Python 3** | Runs the install script. Install it from [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH**. You can check it by running `py --version` in a terminal. |
| **This repository** | Clone or download it, for example to `C:\Tools\Blender_Unreal`. |

All the commands below go in a **PowerShell** or **Command Prompt** window opened in the repository folder.

## 2. Install the Unreal plugin

Close the Unreal editor if it has your project open. Then run:

```
py tools\install.py unreal "C:\Projects\MyGame"
```

Use the folder that contains your `.uproject` file. The script does three things:

- copies the plugin to `C:\Projects\MyGame\Plugins\UnrealLink`
- turns on Python remote execution in `Config\DefaultEngine.ini`
- copies in the shared code both sides need (run the script again after updating the repo)

### Compile the plugin

The plugin contains C++ code, so it has to be compiled once per project. How you do that depends on the project type.

**Your project is a C++ project** (it has a `Source` folder):

1. Double-click the `.uproject` file.
2. Unreal says the **UnrealLink** module is missing and asks whether to rebuild it. Click **Yes**.
3. The first build takes a few minutes. When it's done, the editor opens.

   If the rebuild fails, right-click the `.uproject` file and choose **Generate Visual Studio project files**. Open the
   `.sln` file that appears, set the configuration to **Development Editor**, and build (**Ctrl+Shift+B**).

**Your project is Blueprint-only** (no `Source` folder). You have two options. Option A is the easier one.

- **Option A:** convert the project to C++. Open the project, then choose **Tools → New C++ Class → None → Create
  Class**. Close the editor, and follow the C++ project steps above.
- **Option B:** build the plugin separately, then install the built copy:

  ```
  "C:\Program Files\Epic Games\UE_5.8\Engine\Build\BatchFiles\RunUAT.bat" BuildPlugin ^
      -Plugin="C:\Projects\MyGame\Plugins\UnrealLink\UnrealLink.uplugin" ^
      -Package="C:\Temp\UnrealLinkBuilt" -TargetPlatforms=Win64
  ```

  When the build finishes, replace `C:\Projects\MyGame\Plugins\UnrealLink` with the contents of `C:\Temp\UnrealLinkBuilt`.

### Check it loaded

1. Open the project, then open **Edit → Plugins**, search for **Unreal Link**, and make sure it's enabled.
2. Open **Window → Output Log**. Near the start of the log you should see
   `Unreal Link: pipeline installed in 1 Interchange stack(s)`.
3. Open **Edit → Project Settings → Plugins → Python**. **Enable Remote Execution?** should be ticked. Leave the
   multicast settings at their defaults on Windows.

The first time Blender connects, Windows Firewall may ask whether to let **UnrealEditor** communicate on networks.
Allow it on **private networks**. The connection never leaves your PC, but Windows still asks.

## 3. Install the Blender add-on

1. Build the add-on package:

   ```
   py tools\install.py zip
   ```

   This creates `dist\unreal_link.zip`.
2. In Blender, open **Edit → Preferences → Get Extensions**. Click the **▾** menu at the top right, choose
   **Install from Disk…**, and pick `dist\unreal_link.zip`.

   Alternatively, `py tools\install.py blender --version 5.2` copies the add-on straight into
   `%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\`. Use your Blender version number.
3. Open **Preferences → Add-ons**, find **Unreal Link**, and make sure it's enabled. Expand it and set
   **Unreal project folder** to the folder containing your `.uproject` file, for example `C:\Projects\MyGame\`.
   Leave the multicast settings at their defaults.
4. Preferences normally save automatically. If they don't, click **☰ → Save Preferences**.

## 4. Check that everything is connected

1. Open your Unreal project and wait until the editor has finished loading.
2. In Blender, select your character's armature and press **N**. Open the **Unreal Link** tab.
3. Within about 10 seconds, the top right of the panel should show **Live (UE 5.x)**.

| The header shows | Meaning |
|---|---|
| **Live (UE 5.x)** | Connected. Sends run in Unreal straight away. |
| **Queued** | The project folder is set, but Blender can't reach the editor. Sends are saved and run the next time the editor starts. |
| **Not configured** | The Unreal project folder isn't set in the add-on preferences (step 3). |

Then follow the six steps in the panel: **Identify → Inspect → Map → Fix → Send → Verify**. The Use section of
[README.md](README.md) describes each step. Every button and field in the panel also has a tooltip.

## 5. Updating

After you pull a new version of this repository:

```
py tools\install.py unreal "C:\Projects\MyGame"
py tools\install.py zip
```

- **Unreal:** reopen the project. If the plugin's C++ code changed, Unreal asks to rebuild it again. Python-only
  changes take effect without a rebuild.
- **Blender:** reinstall the zip (**Install from Disk…**), or if you used the `blender` option, press **F3 → Reload
  Scripts**.

## Troubleshooting

| Problem | What to try |
|---|---|
| Header stays on **Queued** while the editor is open | Check that **Enable Remote Execution?** is ticked in Project Settings → Plugins → Python, then restart the editor. Allow UnrealEditor through Windows Firewall (private networks). Make sure the add-on's **Unreal project folder** is the same folder the editor has open. The multicast settings in Blender and Unreal must match; the defaults are `239.0.0.1`, `6766`, `127.0.0.1`. |
| Unreal says **"UnrealLink could not be compiled"** | Check that Visual Studio 2022 has the **Game development with C++** workload and a Windows SDK. The build log is at `C:\Projects\MyGame\Saved\Logs\` or `%LOCALAPPDATA%\UnrealBuildTool\Log.txt`. |
| **"Plugin UnrealLink failed to load because module could not be found"** | The project is Blueprint-only and the plugin wasn't built. Follow Option A or B in section 2. |
| A send shows **Failed** in Verify | The failed checks are listed in the Verify step. The full details are in `C:\Projects\MyGame\UnrealLink\jobs\<job id>\result.json` and in the editor's Output Log (filter by "Unreal Link" or "Interchange"). |
| Queued jobs never run | Queued jobs run once the editor has finished loading. Check the Output Log for `Unreal Link: queued job …`. Pending jobs are listed in `C:\Projects\MyGame\UnrealLink\queue\`. |

## Where Unreal Link keeps its files

Everything is inside the Unreal project, so it's backed up along with the project:

```
C:\Projects\MyGame\UnrealLink\
  config.json     project settings (fps, content root)
  manifests\      history of every verified send
  jobs\           each send: the FBX files, the manifest and result.json
  queue\          sends waiting for the editor to start
  backups\        copies of assets taken before each send (used by Roll back)
```

Blender stores only the project folder path (in the add-on preferences) and each rig's link settings (inside your
`.blend` file).
