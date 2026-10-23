"""Build + sign the two Ask Eve on-the-go Shortcuts: "Eve Start" and "Eve Done".

No secrets inside either file: Eve Done reads the upload token from iCloud Drive/Shortcuts/Ask Eve/upload_token.txt
at run time, so the signed files can be shared by link. Usage: python build_onthego.py OUTDIR
"""
import os, plistlib, subprocess, sys, tempfile, uuid

OUT = sys.argv[1]
BASE = "https://upload.askevepodcast.com"
U = lambda: str(uuid.uuid4()).upper()


def out_ref(uid, name, coerce=None):
    r = {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}
    if coerce:
        r["Aggrandizements"] = [{"Type": "WFCoercionVariableAggrandizement", "CoercionItemClass": coerce}]
    return r


def var(name):
    return {"Type": "ExtensionInput"} if name == "Shortcut Input" else {"Type": "Variable", "VariableName": name}


def attach(ref):
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def text(*parts):
    s, atts = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            atts[f"{{{len(s)}, 1}}"] = p
            s += "\ufffc"
    return {"Value": {"string": s, "attachmentsByRange": atts}, "WFSerializationType": "WFTextTokenString"}


def dict_items(items):  # (key, value, type) 0=text 3=number 4=bool
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": t, "WFKey": text(k), "WFValue": v} for k, v, t in items]},
        "WFSerializationType": "WFDictionaryFieldValue"}


def act(ident, params=None, uid=None):
    p = dict(params or {})
    if uid:
        p["UUID"] = uid
    return {"WFWorkflowActionIdentifier": ident if "." in ident and ident.count(".") > 2 else f"is.workflow.actions.{ident}",
            "WFWorkflowActionParameters": p}


class Flow:
    def __init__(self):
        self.a = []

    def add(self, ident, params=None, uid=None):
        self.a.append(act(ident, params, uid))
        return uid

    def if_(self, ref, cond, **extra):  # 100 has any value, 101 no value, 99 contains
        g = U()
        p = {"GroupingIdentifier": g, "WFControlFlowMode": 0, "WFCondition": cond,
             "WFInput": {"Type": "Variable", "Variable": attach(ref)}}
        p.update(extra)
        self.add("conditional", p)
        return g

    def else_(self, g):
        self.add("conditional", {"GroupingIdentifier": g, "WFControlFlowMode": 1})

    def end(self, g):
        self.add("conditional", {"GroupingIdentifier": g, "WFControlFlowMode": 2}, U())

    def comment(self, s):
        self.add("comment", {"WFCommentActionText": s})

    def notify(self, title, *body):
        self.add("notification", {"WFNotificationActionTitle": title, "WFNotificationActionBody": text(*body),
                                  "WFNotificationActionSound": True})

    def setvar(self, name, ref):
        self.add("setvariable", {"WFVariableName": name, "WFInput": attach(ref)})

    def get_file(self, path, must_exist):
        u = U()
        self.add("documentpicker.open", {"WFGetFilePath": path, "WFFileErrorIfNotFound": must_exist,
                                         "WFShowFilePicker": False}, u)
        return u


def focus(on):
    return act("dnd.set", {"Enabled": 1 if on else 0,
                           "FocusModes": {"DisplayString": "Do Not Disturb", "Identifier": "com.apple.donotdisturb.mode.default"}})


def sign(name, actions, *, share_sheet, icon_color, glyph):
    wf = {
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": icon_color, "WFWorkflowIconGlyphNumber": glyph},
        "WFWorkflowTypes": ["ActionExtension"] if share_sheet else [],
        "WFWorkflowInputContentItemClasses": ["WFAVAssetContentItem", "WFGenericFileContentItem"] if share_sheet else [],
        "WFWorkflowHasShortcutInputVariables": share_sheet,
        "WFWorkflowImportQuestions": [],
        "WFQuickActionSurfaces": [],
    }
    out = os.path.join(OUT, f"{name}.shortcut")
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, f"{name}.shortcut")
        with open(raw, "wb") as f:
            plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
        p = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", raw, "--output", out],
                           capture_output=True, text=True)
        print(name, "sign rc", p.returncode, p.stderr.strip()[-300:], "actions", len(actions),
              "bytes", os.path.getsize(out) if os.path.exists(out) else 0)


# ---------------- Eve Start ----------------
s = Flow()
s.comment("Ask Eve: start a capture. Start Screen Recording (Microphone on) FIRST, then run this. "
          "It turns on Do Not Disturb (a call would stop the recording), marks a capture as running, and opens Grok.")
s.a.append(focus(True))
now, stamp, txt = U(), U(), U()
s.add("date", {"WFDateActionMode": "Current Date"}, now)
s.add("format.date", {"WFDateFormatStyle": "ISO 8601", "WFISO8601IncludeTime": True, "WFDate": text(out_ref(now, "Date"))}, stamp)
s.add("gettext", {"WFTextActionText": text(out_ref(stamp, "Formatted Date"))}, txt)
s.add("documentpicker.save", {"WFAskWhereToSave": False, "WFFileDestinationPath": "Ask Eve/active.txt",
                              "WFSaveFileOverwrite": True, "WFInput": attach(out_ref(txt, "Text"))}, U())
s.add("openapp", {"WFAppIdentifier": "ai.x.GrokApp", "WFSelectedApp": {"BundleIdentifier": "ai.x.GrokApp", "Name": "Grok"}})

# ---------------- Eve Done ----------------
d = Flow()
d.comment("Ask Eve: end a capture. Takes the newest screen recording (or whatever you share to it), keeps only the audio, "
          "uploads it to the Ask Eve pipeline with the Grok share link from the clipboard, and turns Do Not Disturb off. "
          "The pipeline copies everything to Google Drive and emails you when the episode is ready.")
tok = d.get_file("Ask Eve/upload_token.txt", True)
auth = U()
d.add("gettext", {"WFTextActionText": text("Bearer ", out_ref(tok, "File", "WFStringContentItem"))}, auth)

g_in = d.if_(var("Shortcut Input"), 100)
d.setvar("Recording", var("Shortcut Input"))
d.else_(g_in)
active = d.get_file("Ask Eve/active.txt", False)
g_act = d.if_(out_ref(active, "File"), 101)
d.notify("Ask Eve", "No capture is running. Start one with Eve Start, or share a recording to Eve Done.")
d.add("exit")
d.end(g_act)
found = U()
d.add("filter.photos", {
    "WFContentItemFilter": {"Value": {"WFActionParameterFilterPrefix": 1, "WFContentPredicateBoundedDate": False,
                                      "WFActionParameterFilterTemplates": [
                                          {"Operator": 4, "Property": "Is a Screen Recording", "Removable": True, "Values": {"Bool": True}},
                                          {"Operator": 2, "Property": "Creation Date", "Removable": True,   # is after the Eve Start time
                                           "Values": {"Date": attach(out_ref(active, "File", "WFDateContentItem"))}}]},
                            "WFSerializationType": "WFContentPredicateTableTemplate"},
    "WFContentItemSortProperty": "Creation Date", "WFContentItemSortOrder": "Latest First",
    "WFContentItemLimitEnabled": True, "WFContentItemLimitNumber": 1.0}, found)
g_f = d.if_(out_ref(found, "Photos"), 101)
d.notify("Ask Eve", "No finished screen recording since Eve Start yet, so nothing was uploaded and the capture is still open. Stop the screen recording (red pill → Stop), then leave Grok or run Eve Done.")
d.add("exit")
d.end(g_f)
d.setvar("Recording", out_ref(found, "Photos"))
d.end(g_in)

audio = U()
d.add("encodemedia", {"WFMedia": attach(var("Recording")), "WFMediaAudioOnly": True}, audio)
clip, match = U(), U()
d.add("getclipboard", {}, clip)
d.add("text.match", {"WFMatchTextPattern": r"https://grok\.com/share/[A-Za-z0-9_-]+", "WFMatchTextCaseSensitive": False,
                     "text": text(out_ref(clip, "Clipboard"))}, match)
g_m = d.if_(out_ref(match, "Matches"), 101)
asked = U()
d.add("ask", {"WFAskActionPrompt": "Grok share link (Grok → ⋯ → Share → Copy link, then paste)", "WFInputType": "URL"}, asked)
d.setvar("ShareURL", out_ref(asked, "Provided Input"))
d.else_(g_m)
d.setvar("ShareURL", out_ref(match, "Matches"))
d.end(g_m)

now, day, slug = U(), U(), U()
d.add("date", {"WFDateActionMode": "Current Date"}, now)
d.add("format.date", {"WFDateFormatStyle": "Custom", "WFDateFormat": "yyyy-MM-dd", "WFDate": text(out_ref(now, "Date"))}, day)
d.add("format.date", {"WFDateFormatStyle": "Custom", "WFDateFormat": "'walk-'HHmm", "WFDate": text(out_ref(now, "Date"))}, slug)
pre, put_url, put, ing, err = U(), U(), U(), U(), U()
hdr_auth = dict_items([("Authorization", text(out_ref(auth, "Text")), 0)])
d.add("downloadurl", {"WFURL": f"{BASE}/presign", "WFHTTPMethod": "POST", "ShowHeaders": True, "WFHTTPHeaders": hdr_auth,
                      "WFHTTPBodyType": "JSON", "WFJSONValues": dict_items([
                          ("date", text(out_ref(day, "Formatted Date")), 0), ("slug", text(out_ref(slug, "Formatted Date")), 0),
                          ("ext", text("m4a"), 0)])}, pre)
d.add("getvalueforkey", {"WFInput": attach(out_ref(pre, "Contents of URL")), "WFDictionaryKey": "put_url"}, put_url)
g_p = d.if_(out_ref(put_url, "Dictionary Value"), 101)
d.notify("Ask Eve upload failed", "Could not get an upload URL: ", out_ref(pre, "Contents of URL"))
d.add("exit")
d.end(g_p)
d.add("downloadurl", {"WFURL": text(out_ref(put_url, "Dictionary Value")), "WFHTTPMethod": "PUT", "ShowHeaders": True,
                      "WFHTTPHeaders": dict_items([("Content-Type", text("audio/mp4"), 0)]),
                      "WFHTTPBodyType": "File", "WFRequestVariable": attach(out_ref(audio, "Encoded Media"))}, put)
d.add("downloadurl", {"WFURL": f"{BASE}/ingest", "WFHTTPMethod": "POST", "ShowHeaders": True, "WFHTTPHeaders": hdr_auth,
                      "WFHTTPBodyType": "JSON", "WFJSONValues": dict_items([
                          ("date", text(out_ref(day, "Formatted Date")), 0), ("slug", text(out_ref(slug, "Formatted Date")), 0),
                          ("ext", text("m4a"), 0), ("share_url", text(var("ShareURL")), 0)])}, ing)
d.add("getvalueforkey", {"WFInput": attach(out_ref(ing, "Contents of URL")), "WFDictionaryKey": "error"}, err)
g_e = d.if_(out_ref(err, "Dictionary Value"), 100)
d.notify("Ask Eve upload failed", out_ref(err, "Dictionary Value"), " (", out_ref(ing, "Contents of URL"), ")")
d.add("exit")
d.end(g_e)
active2 = d.get_file("Ask Eve/active.txt", False)
g_a2 = d.if_(out_ref(active2, "File"), 100)
d.add("file.delete", {"WFInput": attach(out_ref(active2, "File")), "WFDeleteFileConfirmDeletion": False})
d.end(g_a2)
d.a.append(focus(False))
d.notify("Ask Eve: uploaded", "Eve is processing ", out_ref(slug, "Formatted Date"),
         ". You'll get an email with the Drive links when the episode is ready (about 10-20 min).")

os.makedirs(OUT, exist_ok=True)
sign("Eve Start", s.a, share_sheet=False, icon_color=4292093695, glyph=59446)
sign("Eve Done", d.a, share_sheet=True, icon_color=4282601983, glyph=59511)
