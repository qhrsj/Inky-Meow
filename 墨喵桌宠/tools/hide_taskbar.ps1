param([long]$hwin)

# Add WS_EX_TOOLWINDOW and remove WS_EX_APPWINDOW.
# The window stays focusable (mouse click/drag works) but is hidden from
# the taskbar and Alt-Tab.
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class MomiaoWin { [DllImport("user32.dll", EntryPoint="GetWindowLongPtrW")] public static extern IntPtr GetWindowLongPtr(IntPtr h, int i); [DllImport("user32.dll", EntryPoint="SetWindowLongPtrW")] public static extern IntPtr SetWindowLongPtr(IntPtr h, int i, IntPtr v); [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f); }' -ErrorAction Stop

$hwnd = [IntPtr]$hwin
$ex = [MomiaoWin]::GetWindowLongPtr($hwnd, -20).ToInt64()
$ex = ($ex -bor 0x80) -band (-bnot 0x40000) -band (-bnot 0x08000000)
[MomiaoWin]::SetWindowLongPtr($hwnd, -20, [IntPtr]$ex) | Out-Null
[MomiaoWin]::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, 0, 0, 0x27) | Out-Null
