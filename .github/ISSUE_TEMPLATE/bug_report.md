---
name: Bug report
about: Something doesn't work as expected
labels: bug
---

**What happened**
A clear description of the problem.

**What you expected**

**Setup**
- TV model and year:
- Tizen version (Settings → Support → About This TV):
- Controller:
- How the PC reaches the TV (HDMI cable, fiber HDMI, extender model):
- PC operating system (Windows version, or Linux distribution):
- Padthru version (TV info panel; `padthru --version` on Linux):

**Info panel**
What the TV's info panel (Red button) shows for PC, Picture and Controller.

**Log**
The relevant lines from the PC log:
- Windows: `%LOCALAPPDATA%\Padthru\padthru.log` (tray menu → Open log). Running `Padthru.exe --debug` adds more detail.
- Linux: `journalctl --user -u padthru`. See [docs/linux.md](https://github.com/labanmne/padthru/blob/main/docs/linux.md#everyday-use) for debug logging.
