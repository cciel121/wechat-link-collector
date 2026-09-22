// FiddlerScript 片段：微信相关请求实时落盘，省去手动保存 saz
//
// 安装位置：Fiddler Classic 菜单 Rules > Customize Rules…
//   1) 【先备份】把弹出的 CustomRules.js 另存一份，改错了能还原
//   2) 把下面两个函数（mpEsc / mpDump）连同 static 变量一起，
//      加进文件末尾的 class Handlers { ... } 里面
//   3) 在你原本就有的 OnBeforeResponse 函数体【第一行】插入：  mpDump(oSession);
//      注意：不要新写一个 OnBeforeResponse，重名会导致脚本加载失败
//   4) Ctrl+S 保存，Fiddler 会自动重新加载
//
// 产出：mpOut 指向的 jsonl，可直接交给 analyze_capture.py 分析
//
// ⚠️ 本片段未在作者本机验证（本机无 Fiddler）。改动极小且所有 IO 都包了 try/catch，
//    但请务必先备份 CustomRules.js。如果你不想碰脚本，直接走
//    File > Save > All Sessions 存成 .saz，analyze_capture.py 一样能解析。

static var mpOut = "C:\\cap\\fiddler_wechat.jsonl";   // 改成你的路径，注意反斜杠要写两个

// 只记这些域名；想全局记录就把过滤条件删掉
static function mpKeep(h: String): Boolean {
    return h.IndexOf("weixin") >= 0 || h.IndexOf("mmbiz") >= 0;
}

static function mpEsc(s: String): String {
    if (s == null) return "";
    return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ");
}

static function mpDump(oSession: Session) {
    try {
        var h = oSession.hostname;
        if (h == null) return;
        h = h.ToLower();
        if (!mpKeep(h)) return;

        var url = oSession.fullUrl;
        if (url == null) url = "";

        var body = "";
        try { body = oSession.GetResponseBodyAsString(); } catch (e) { body = ""; }
        if (body == null) body = "";

        // 响应体里内嵌的文章链接 —— 列表接口的特征
        var linksJson = "";
        try {
            var re = new RegExp("https?://mp\\.weixin\\.qq\\.com/s[^\\s\"'\\\\<>]{4,}", "gi");
            var m = body.match(re);
            if (m != null) {
                var seenMap = {};
                var arr = [];
                for (var i = 0; i < m.length; i++) {
                    if (!seenMap[m[i]]) {
                        seenMap[m[i]] = true;
                        arr[arr.length] = "\"" + mpEsc(m[i]) + "\"";
                    }
                }
                if (arr.length > 0) linksJson = ",\"embedded_links\":[" + arr.join(",") + "]";
            }
        } catch (e) {}

        var reqLine = "";
        try { reqLine = oSession.oRequest.headers.HTTPMethod; } catch (e) {}

        var line = "{\"time\":\"" + System.DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\""
            + ",\"method\":\"" + mpEsc(reqLine) + "\""
            + ",\"host\":\"" + mpEsc(h) + "\""
            + ",\"path\":\"" + mpEsc(oSession.PathAndQuery) + "\""
            + ",\"status\":" + oSession.responseCode
            + ",\"url\":\"" + mpEsc(url) + "\""
            + ",\"body_len\":" + body.Length
            + linksJson + "}\r\n";

        System.IO.File.AppendAllText(mpOut, line, System.Text.Encoding.UTF8);
    } catch (e) {
        // 静默失败：采集器不该因为写文件失败而打断你的抓包
    }
}
