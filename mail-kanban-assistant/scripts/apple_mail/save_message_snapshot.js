#!/usr/bin/osascript -l JavaScript
/*
  Apple Mail (JXA) — write JSON snapshot files into MAILDROP_INCOMING (absolute path).

  Export before use:
    export MAILDROP_INCOMING="/ABS/PATH/mail-kanban-assistant/data/maildrop/incoming"

  Run from Script Editor with Mail frontmost and messages selected, or wire into a Mail rule.

  MVP limitations (honest best effort):
    - message_id / thread_id may be synthetic or empty depending on Mail scripting
    - body_text comes from Mail's content(); HTML-heavy mail may be noisy
    - attachments are metadata-only (count), not extracted
*/

ObjC.import("Foundation");

function getenv(name) {
  try {
    return $.getenv(name) || "";
  } catch (e) {
    return "";
  }
}

function isoNow() {
  return new Date().toISOString();
}

function safeStr(v) {
  if (v === null || v === undefined) return "";
  return String(v);
}

function writeUtf8File(path, text) {
  var err = Ref();
  var ok = $.NSString.alloc.initWithUTF8String(text).writeToFileAtomicallyEncodingError(
    path,
    true,
    $.NSUTF8StringEncoding,
    err
  );
  if (!ok) {
    var msg = err && err[0] ? err[0].localizedDescription.js : "unknown error";
    throw new Error("write failed: " + msg);
  }
}

function main() {
  var incoming = getenv("MAILDROP_INCOMING").replace(/\/$/, "");
  if (!incoming) {
    throw new Error("MAILDROP_INCOMING is not set (absolute path to maildrop/incoming required)");
  }

  var Mail = Application("Mail");
  var sel = Mail.selection();
  if (!sel || sel.length === 0) {
    throw new Error("No messages selected in Mail");
  }

  for (var i = 0; i < sel.length; i++) {
    var m = sel[i];
    var snapshotId = "am-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);

    var subject = safeStr(m.subject());
    var sender = null;
    try {
      sender = m.sender();
    } catch (e0) {}
    var senderName = null;
    var senderEmail = null;
    if (sender) {
      try {
        senderName = safeStr(sender.name());
      } catch (e1) {}
      try {
        senderEmail = safeStr(sender.address());
      } catch (e2) {}
    }

    var toList = [];
    try {
      var tos = m.toRecipients();
      if (tos) {
        for (var t = 0; t < tos.length; t++) {
          try {
            toList.push(safeStr(tos[t].address()));
          } catch (e3) {}
        }
      }
    } catch (e4) {}

    var ccList = [];
    try {
      var ccs = m.ccRecipients();
      if (ccs) {
        for (var c = 0; c < ccs.length; c++) {
          try {
            ccList.push(safeStr(ccs[c].address()));
          } catch (e5) {}
        }
      }
    } catch (e6) {}

    var bccList = [];
    try {
      var bccs = m.bccRecipients();
      if (bccs) {
        for (var b = 0; b < bccs.length; b++) {
          try {
            bccList.push(safeStr(bccs[b].address()));
          } catch (e7) {}
        }
      }
    } catch (e8) {}

    var bodyText = "";
    try {
      bodyText = safeStr(m.content());
    } catch (e9) {}
    if (!bodyText) {
      bodyText = "(empty body — Mail scripting did not return content)";
    }

    var messageId = "";
    try {
      messageId = safeStr(m.messageId());
    } catch (e10) {}
    if (!messageId) {
      messageId = "synthetic:" + snapshotId;
    }

    var threadId = null;
    try {
      var tid = safeStr(m.id());
      threadId = tid || null;
    } catch (e11) {}

    var mailboxName = null;
    var accountName = null;
    try {
      mailboxName = safeStr(m.mailbox().name());
    } catch (e12) {}
    try {
      accountName = safeStr(m.mailbox().account().name());
    } catch (e13) {}

    var unread = null;
    var flagged = null;
    try {
      unread = m.readStatus() === "unread";
    } catch (e14) {}
    try {
      flagged = !!m.flaggedStatus();
    } catch (e15) {}

    var receivedAt = null;
    try {
      var dr = m.dateReceived();
      if (dr) receivedAt = dr.toISOString();
    } catch (e16) {}

    var collectedAt = isoNow();

    var attachmentCount = 0;
    try {
      var atts = m.mailAttachments();
      if (atts) attachmentCount = atts.length;
    } catch (e17) {}

    var preview = bodyText.length > 400 ? bodyText.slice(0, 400) : null;

    var doc = {
      snapshot_id: snapshotId,
      source: "apple_mail_drop",
      message_id: messageId,
      thread_id: threadId,
      mailbox_name: mailboxName,
      account_name: accountName,
      subject: subject || null,
      sender_name: senderName,
      sender_email: senderEmail,
      to: toList,
      cc: ccList,
      bcc: bccList,
      date: null,
      body_text: bodyText,
      body_preview: preview,
      unread: unread,
      flagged: flagged,
      received_at: receivedAt,
      collected_at: collectedAt,
      attachments_summary:
        attachmentCount > 0 ? [{ kind: "count_only", count: attachmentCount }] : null,
      raw_metadata: { script: "save_message_snapshot.js" },
    };

    var json = JSON.stringify(doc, null, 2);
    var path = incoming + "/" + snapshotId + ".json";
    writeUtf8File(path, json);
  }

  return "Wrote " + sel.length + " snapshot(s) to " + incoming;
}

main();
