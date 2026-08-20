# @runtime PowerShell
# GUI probe driver: launches a Windows GUI executable, optionally sends
# input text + Enter, and reports back the text of every visible window
# and control belonging to that process — including MessageBox dialogs,
# which is where "Correct!"/"Wrong..."-style feedback typically lives.
#
# This exists because many CTF binaries are interactive GUI apps that
# cannot be driven via piped stdin — this is the actual mechanism for
# verifying a candidate answer against real program behavior instead of
# reporting a plausible-looking guess as final.
#
# Usage (from the bridge's exec, as a command array — no shell quoting
# concerns since this never passes through bash):
#   powershell -NoProfile -ExecutionPolicy Bypass -File gui_probe.ps1
#     -ExePath "C:\Samples\target.exe" -InputText "candidate answer"
#
# Output: JSON on stdout with initial_windows (right after launch) and
# after_input_windows (after sending InputText + Enter and waiting) —
# each window entry includes its title, class, and any child controls'
# text (buttons, static labels, message text).

param(
    [Parameter(Mandatory=$true)][string]$ExePath,
    [Parameter(Mandatory=$false)][string]$InputText = "",
    [Parameter(Mandatory=$false)][int]$WaitForWindowSeconds = 5,
    [Parameter(Mandatory=$false)][int]$WaitAfterInputSeconds = 3
)

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;

public class Win32Probe {
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    public static List<IntPtr> GetWindowsForProcess(uint pid) {
        List<IntPtr> result = new List<IntPtr>();
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
            uint windowPid;
            GetWindowThreadProcessId(hWnd, out windowPid);
            if (windowPid == pid && IsWindowVisible(hWnd)) {
                result.Add(hWnd);
            }
            return true;
        }, IntPtr.Zero);
        return result;
    }

    public static string GetText(IntPtr hWnd) {
        StringBuilder sb = new StringBuilder(1024);
        GetWindowText(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static string GetClass(IntPtr hWnd) {
        StringBuilder sb = new StringBuilder(256);
        GetClassName(hWnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static List<IntPtr> GetChildWindows(IntPtr hWndParent) {
        List<IntPtr> result = new List<IntPtr>();
        EnumChildWindows(hWndParent, delegate(IntPtr hWnd, IntPtr lParam) {
            result.Add(hWnd);
            return true;
        }, IntPtr.Zero);
        return result;
    }
}
"@

Add-Type -AssemblyName System.Windows.Forms

function Get-WindowSnapshot($pid) {
    $windows = [Win32Probe]::GetWindowsForProcess([uint32]$pid)
    $snapshot = @()
    foreach ($w in $windows) {
        $title = [Win32Probe]::GetText($w)
        $class = [Win32Probe]::GetClass($w)
        $children = @()
        foreach ($c in [Win32Probe]::GetChildWindows($w)) {
            $ctext = [Win32Probe]::GetText($c)
            $cclass = [Win32Probe]::GetClass($c)
            if ($ctext -ne "") {
                $children += [PSCustomObject]@{ class = $cclass; text = $ctext }
            }
        }
        $snapshot += [PSCustomObject]@{
            handle = $w.ToInt64()
            title = $title
            class = $class
            children = $children
        }
    }
    return $snapshot
}

function Escape-SendKeys($text) {
    # SendKeys treats + ^ % ~ ( ) { } [ ] as special — wrap each literal
    # occurrence in braces so arbitrary candidate text is sent literally.
    $special = @('+','^','%','~','(',')','{','}','[',']')
    $result = $text
    foreach ($ch in $special) {
        $result = $result.Replace($ch, "{$ch}")
    }
    return $result
}

$proc = Start-Process -FilePath $ExePath -PassThru
Start-Sleep -Seconds $WaitForWindowSeconds

$result = [ordered]@{}
$result.initial_windows = Get-WindowSnapshot $proc.Id

if ($InputText -ne "") {
    $windows = [Win32Probe]::GetWindowsForProcess([uint32]$proc.Id)
    if ($windows.Count -gt 0) {
        [Win32Probe]::SetForegroundWindow($windows[0]) | Out-Null
        Start-Sleep -Milliseconds 500
        $escaped = Escape-SendKeys $InputText
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        Start-Sleep -Seconds $WaitAfterInputSeconds
    }
}

$result.after_input_windows = Get-WindowSnapshot $proc.Id

# Capture the actual rendered screen, not just window/control text — this
# catches custom-painted GDI content that GetWindowText can't see, and
# works even when window enumeration under the target's own PID comes up
# empty (e.g. a system error dialog for a missing DLL, which is often
# owned by a different process than the one that failed to launch).
try {
    Add-Type -AssemblyName System.Drawing
    $screenBounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $screenBounds.Width, $screenBounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($screenBounds.Location, [System.Drawing.Point]::Empty, $screenBounds.Size)
    $screenshotPath = "C:\Tools\gui_probe_screenshot.png"
    $bitmap.Save($screenshotPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
    $result.screenshot_path = $screenshotPath
} catch {
    $result.screenshot_error = $_.Exception.Message
}

try {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension($ExePath)) -Force -ErrorAction SilentlyContinue
} catch {}

$result | ConvertTo-Json -Depth 6 -Compress
