// Compiled DXF completion scanner for export_dwg_exchange_dxf.ps1 (loaded with Add-Type -Path).
// Same rules as the former PowerShell loop: paired group code / value lines, SECTION/ENDSEC
// balance, an ENTITIES section and a single trailing EOF. PowerShell's per-line loop needed
// minutes on 200 MB exports; this scan is seconds. C# 5 syntax only: Windows PowerShell 5.1
// compiles it with the legacy CodeDom compiler.
using System;
using System.IO;
using System.Text;

public static class QtoDxfCompletion
{
    // Returns null when the file is a complete ASCII DXF, otherwise the failure message.
    public static string Check(string path)
    {
        using (StreamReader reader = new StreamReader(path, Encoding.UTF8, true, 1 << 20))
        {
            bool inSection = false;
            bool sawEntities = false;
            bool sawEof = false;
            string codeLine;
            while ((codeLine = reader.ReadLine()) != null)
            {
                if (sawEof)
                {
                    if (codeLine.Trim().Length > 0) { return "DXF EOF后仍有数据。"; }
                    continue;
                }
                string valueLine = reader.ReadLine();
                if (valueLine == null) { return "DXF组码缺值，文件可能截断。"; }
                string code = codeLine.Trim();
                string value = valueLine.Trim();
                if (!IsDigits(code)) { return "DXF组码非法或不是ASCII交换格式。"; }
                if (code == "0" && value == "SECTION")
                {
                    if (inSection) { return "DXF节未关闭。"; }
                    inSection = true;
                    string sectionCode = reader.ReadLine();
                    string sectionName = reader.ReadLine();
                    if (sectionCode == null || sectionCode.Trim() != "2" || sectionName == null) { return "DXF节名缺失。"; }
                    if (sectionName.Trim() == "ENTITIES") { sawEntities = true; }
                }
                else if (code == "0" && value == "ENDSEC")
                {
                    if (!inSection) { return "DXF存在无对应起点的ENDSEC。"; }
                    inSection = false;
                }
                else if (code == "0" && value == "EOF")
                {
                    if (inSection) { return "DXF节未闭合即结束。"; }
                    sawEof = true;
                }
            }
            if (inSection || !sawEof || !sawEntities) { return "DXF未完整导出：缺少闭合ENDSEC、ENTITIES或EOF。"; }
            return null;
        }
    }

    private static bool IsDigits(string text)
    {
        if (text.Length == 0) { return false; }
        foreach (char c in text)
        {
            if (!char.IsDigit(c)) { return false; }
        }
        return true;
    }
}
