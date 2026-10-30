/**
 * Paste this into Extensions > Apps Script for the "Daily Tickets" sheet.
 *
 * Setup:
 *   1. Project Settings (gear icon, left sidebar) -> Script Properties ->
 *      Add property: SHARED_SECRET = <make up a random string>.
 *      Put that same string in ticket_notifier.py's .env as
 *      GOOGLE_SHEETS_WEBHOOK_SECRET.
 *   2. Deploy -> New deployment -> type "Web app".
 *      Execute as: Me. Who has access: Anyone.
 *      Deploy, authorize when prompted, copy the Web app URL into .env as
 *      GOOGLE_SHEETS_WEBHOOK_URL.
 *   3. If you edit this script later, you must create a NEW deployment (or
 *      use "Manage deployments" -> edit -> new version) for changes to take
 *      effect on the existing URL.
 */

var HEADERS = ["Ticket ID", "Subject", "Room", "Category", "Created",
               "Status", "Assigned To", "Last Reply", "Availability"];

function doPost(e) {
  var result;
  try {
    var payload = JSON.parse(e.postData.contents);
    var expectedSecret = PropertiesService.getScriptProperties().getProperty("SHARED_SECRET");

    if (!expectedSecret || payload.secret !== expectedSecret) {
      result = { error: "unauthorized" };
    } else {
      result = upsertTickets(payload.tickets || []);
    }
  } catch (err) {
    result = { error: String(err) };
  }
  return ContentService.createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
}

function upsertTickets(tickets) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  var data = sheet.getDataRange().getValues();
  var headerOk = data.length > 0 && data[0].indexOf("Availability") !== -1;
  if (!headerOk) {
    // Either a brand-new empty sheet, or an older sheet from before the
    // Availability column existed -- write/migrate the header either way.
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    data[0] = HEADERS;
  }

  var idToRow = {};
  for (var i = 1; i < data.length; i++) {
    idToRow[data[i][0]] = i + 1; // 1-indexed sheet row number
  }

  var newRows = [];
  var updatedCount = 0;

  tickets.forEach(function (t) {
    var liveFields = [t.status, t.assigned_to, t.last_reply];
    var rowNum = idToRow[t.id];

    if (!rowNum) {
      newRows.push([t.id, t.subject, t.room, t.category, t.created,
                     t.status, t.assigned_to, t.last_reply, t.availability || ""]);
    } else {
      var existingRow = data[rowNum - 1];
      var changed = String(existingRow[5]) !== String(liveFields[0]) ||
                    String(existingRow[6]) !== String(liveFields[1]) ||
                    String(existingRow[7]) !== String(liveFields[2]);
      if (changed) {
        sheet.getRange(rowNum, 6, 1, 3).setValues([liveFields]);
        updatedCount++;
      }
    }
  });

  if (newRows.length > 0) {
    sheet.getRange(sheet.getLastRow() + 1, 1, newRows.length, HEADERS.length)
        .setValues(newRows);
  }

  return { new: newRows.length, updated: updatedCount };
}
