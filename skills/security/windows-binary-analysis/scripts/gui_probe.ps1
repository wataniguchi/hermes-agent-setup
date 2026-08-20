# @runtime PowerShell
# GUI probe driver: launches a Windows GUI executable and submits a
# SEQUENCE of inputs one after another (each followed by Enter), capturing
# window/control text after every step — not just once. This exists
# because some challenges are multi-stage (answer question 1, then a new
# question/dialog appears, then question 2, etc.) and the final result
# (e.g. a flag) may only appear after ALL steps are answered correctly in
# order. A single-shot probe cannot reach that state at all.
#
# This is the actual mechanism for verifying a candidate answer sequence
# against real program behavior instead of reporting a plausible-looking
# guess as final.
#
# Usage (from the bridge's exec, as a command array — no shell quoting
# concerns since this never passes through bash):
#   powershell -NoProfile -ExecutionPolicy Bypass -File gui_probe.ps1
#     -ExePath "C:\Samples\target.exe" -InputSequence "Human;Greenland;42"
#
# -InputSequence: semicolon-separated list of inputs to submit in order.
# For a single-input case, this is equivalent to the old -InputText
# behavior (still accepted for backward compatibility).
#
# Output: JSON on stdout with initial_windows (right after launch) and
# steps (one entry per input submitted, each showing the window/control
# state observed after that step) — the LAST step's state is where a
# final success message/flag is most likely to appear, but earlier steps
# matter too (e.g. to confirm step 1 said "Correct" before step 2 was
# even attempted).

param(
    [Parameter(Mandatory=$true)][string]$ExePath,
    [Parameter(Mandatory=$false)][string]$InputText = "",
    [Parameter(Mandatory=$false)][string]$InputSequence = "",
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

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

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

    public static uint GetWindowProcessId(IntPtr hWnd) {
        uint pid;
        GetWindowThreadProcessId(hWnd, out pid);
        return pid;
    }
}
"@

Add-Type -AssemblyName System.Windows.Forms

function Get-WindowSnapshot($procId) {
    $windows = [Win32Probe]::GetWindowsForProcess([uint32]$procId)
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

function Send-ToActiveWindow($procId, $text) {
    # Prefer the actual foreground window if it belongs to our process
    # (correctly targets whichever dialog is currently active, which
    # matters a lot once multiple dialogs appear over a sequence) —
    # fall back to the first visible window if the foreground check
    # doesn't match (e.g. focus hasn't settled yet).
    $fg = [Win32Probe]::GetForegroundWindow()
    $target = $null
    if ([Win32Probe]::GetWindowProcessId($fg) -eq $procId) {
        $target = $fg
    } else {
        $windows = [Win32Probe]::GetWindowsForProcess([uint32]$procId)
        if ($windows.Count -gt 0) { $target = $windows[0] }
    }
    if ($target -ne $null) {
        [Win32Probe]::SetForegroundWindow($target) | Out-Null
        Start-Sleep -Milliseconds 500
        $escaped = Escape-SendKeys $text
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        return $true
    }
    return $false
}

# Build the input sequence: prefer -InputSequence, fall back to legacy
# single -InputText for backward compatibility.
$inputs = @()
if ($InputSequence -ne "") {
    $inputs = $InputSequence -split ";"
} elseif ($InputText -ne "") {
    $inputs = @($InputText)
}

$proc = Start-Process -FilePath $ExePath -PassThru
Start-Sleep -Seconds $WaitForWindowSeconds

$result = [ordered]@{}
$result.initial_windows = Get-WindowSnapshot $proc.Id

$steps = @()
foreach ($candidate in $inputs) {
    $sent = Send-ToActiveWindow $proc.Id $candidate
    Start-Sleep -Seconds $WaitAfterInputSeconds
    $steps += [PSCustomObject]@{
        input = $candidate
        sent = $sent
        windows_after = Get-WindowSnapshot $proc.Id
    }
}
$result.steps = $steps

# Capture the actual rendered screen after the full sequence — catches
# custom-painted GDI content that GetWindowText can't see, and is useful
# for a human operator to inspect directly (the model driving this skill
# is very likely text-only and cannot view this itself).
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

$result | ConvertTo-Json -Depth 8 -Compress
