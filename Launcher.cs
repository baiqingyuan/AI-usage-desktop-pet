using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("AI 用量挂件")]
[assembly: AssemblyDescription("AI Usage Desktop Companion")]
[assembly: AssemblyVersion("0.3.0.0")]

internal static class Launcher
{
    [STAThread]
    private static void Main(string[] args)
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(Directory.GetParent(folder.TrimEnd(Path.DirectorySeparatorChar)).FullName, ".venv", "Scripts", "pythonw.exe");
        if (!File.Exists(python))
        {
            python = null;
            foreach (string version in new[] { "Python314", "Python313", "Python312", "Python311", "Python310" })
            {
                string candidate = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", version, "pythonw.exe");
                if (File.Exists(candidate)) { python = candidate; break; }
            }
        }
        if (python == null)
        {
            MessageBox.Show("需要 Python 3.10 或以上版本，请安装后重新启动。", "AI 用量挂件", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try
        {
            var start = new ProcessStartInfo();
            start.FileName = python;
            start.Arguments = "\"" + Path.Combine(folder, "app.py") + "\"";
            if (Array.IndexOf(args, "--details") >= 0) start.Arguments += " --details";
            start.WorkingDirectory = folder;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            Process.Start(start);
        }
        catch (Exception)
        {
            MessageBox.Show("启动失败，请检查工具文件是否齐全。", "AI 用量挂件", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
