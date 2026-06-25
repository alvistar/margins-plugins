---
description: Work through the open review comments on your Margins workspace — address each in plain language, update the document, and resolve the thread.
---

# Address review comments

Help the user respond to the comments reviewers left on their Margins workspace, entirely in
Cowork. Plain language throughout.

## 1. Find the workspace

Read `workspaceId` from `.margins.json` in the folder. If there's no binding yet, tell the
user to run `/margins-sync` first to connect the folder.

## 2. List open discussions

Call `list_discussions` with the `workspaceId` and `status: "open"`. If there are none, say so
("No open comments right now") and stop.

## 3. Work through each comment

For each open discussion:

1. Show the user the comment (and which document/section it's on). Use `get_document` to read
   the relevant document if you need context.
2. Help them decide how to address it, and revise the **local** markdown file accordingly.
3. Publish the revision:
   - If the desktop app owns this folder (`.margins.json` `contentOwner: "desktop"`), the file
     change will sync on its own — just save it.
   - Otherwise call **`update_document`** with the `workspaceId`, the document `path`, the full
     new `content`, and **`parentSha`** = the workspace `head` from when you read this document
     (call `get_workspace_manifest` once at the start to capture it). Passing `parentSha` means a
     reviewer or another session that edited the same doc meanwhile is **merged**, not clobbered.
     - **After each successful write, set `parentSha` to the `head` returned in the response**
       before editing the next document — each write advances the head, so reusing the start-of-
       session head would force an unnecessary merge on every later edit.
     - If the result comes back with a non-empty **`conflicts`** list, your edit did **not** land
       (the same lines changed on both sides). Read the current version with `get_document`,
       combine the two, then call `update_document` again using the **`head` from the conflict
       response** as `parentSha` (the response carries it — `get_document` does not). Never
       discard either side.
4. Optionally reply to the reviewer with `reply_to_discussion` (what changed), then mark it
   done with **`resolve_discussion`** (a short summary of how you addressed it).

## 4. Wrap up

Summarize what was addressed and resolved, and remind the user that if they made content
changes outside the desktop-synced flow, they may want to run `/margins-sync` to publish them.
