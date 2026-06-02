# fetch_daily_reports.ps1
# Fetch .md attachments from the configured daily-report Outlook folder,
# save originals to the archive directory, and tag processed emails
# with an Outlook Category so re-runs can skip them.
#
# Invoked from WSL via scripts/fetch_daily_reports.py; outputs JSON to stdout.
# All tenant-specific values are passed in as parameters (config-driven, zero hard-code).
#
# Required parameters:
#   -TargetDate        yyyy-MM-dd (e.g. 2026-04-21)
#   -OutlookAccount    <your Outlook account email>
#   -FolderName        <daily-report subfolder name under the inbox>
#   -TeamMembers       comma-separated display names (matched by sender suffix)
#   -ArchiveDir        Windows path (e.g. C:\...\data\daily_reports)
#
# Optional:
#   -InboxName         inbox display name (default "Inbox"; e.g. "收件匣" for zh-TW Outlook)
#   -Category          processed-tag category (default "AI Processed")
#   -AttachmentPattern attachment filename regex (default daily_work_log_<date>.md)
#   -SubjectPattern    date-in-subject regex (default language-neutral date matcher)

param(
  [Parameter(Mandatory=$true)][string]$TargetDate,
  [Parameter(Mandatory=$true)][string]$OutlookAccount,
  [Parameter(Mandatory=$true)][string]$FolderName,
  [Parameter(Mandatory=$true)][string]$TeamMembers,
  [Parameter(Mandatory=$true)][string]$ArchiveDir,
  [string]$InboxName = "Inbox",
  [string]$Category = "AI Processed",
  [string]$AttachmentPattern = "daily_work_log_(\d{4}-\d{2}-\d{2})\.md",
  [string]$SubjectPattern = "(\d{4})[/-](\d{2})[/-](\d{2})"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$members = $TeamMembers -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$targetDateSlash = (Get-Date $TargetDate).ToString("yyyy/MM/dd")

$result = [ordered]@{
  target_date = $TargetDate
  folder      = $FolderName
  archive_dir = $ArchiveDir
  results     = @()
  missing     = @()
  errors      = @()
}

try {
  $outlook = New-Object -ComObject Outlook.Application
  $ns = $outlook.GetNamespace("MAPI")

  $root = $ns.Folders.Item($OutlookAccount)
  $inbox = $root.Folders.Item($InboxName)
  $folder = $inbox.Folders.Item($FolderName)

  if (-not (Test-Path $ArchiveDir)) {
    New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null
  }
  $dayDir = Join-Path $ArchiveDir $TargetDate
  if (-not (Test-Path $dayDir)) {
    New-Item -ItemType Directory -Path $dayDir -Force | Out-Null
  }

  # Sort by SentOn descending so "take latest" falls out naturally.
  $items = @($folder.Items)
  $items = $items | Sort-Object -Property SentOn -Descending

  $seenPerSender = @{}

  foreach ($item in $items) {
    try {
      $subject = [string]$item.Subject
      $sender  = [string]$item.SenderName
      if (-not $subject -or -not $sender) { continue }

      $matchedName = $null
      foreach ($name in $members) {
        if ($sender.EndsWith($name)) { $matchedName = $name; break }
      }
      if (-not $matchedName) { continue }

      # Extract target-date candidate from subject first.
      $emailTargetDate = $null
      $targetSource = $null
      if ($subject -match $SubjectPattern) {
        $emailTargetDate = "{0}-{1}-{2}" -f $Matches[1], $Matches[2], $Matches[3]
        $targetSource = "subject"
      }

      # Find .md attachment that matches the expected filename pattern.
      $mdAttachment = $null
      $mdDate = $null
      if ($item.Attachments.Count -gt 0) {
        for ($i = 1; $i -le $item.Attachments.Count; $i++) {
          $att = $item.Attachments.Item($i)
          if ($att.FileName -match $AttachmentPattern) {
            $mdAttachment = $att
            $mdDate = $Matches[1]
            break
          }
        }
      }

      # Filename date is authoritative when present.
      if ($mdDate) {
        $emailTargetDate = $mdDate
        $targetSource = "filename"
      }

      if ($emailTargetDate -ne $TargetDate) { continue }

      if ($seenPerSender.ContainsKey($matchedName)) { continue }
      $seenPerSender[$matchedName] = $true

      $alreadyProcessed = ($item.Categories -and ($item.Categories -split ',' | ForEach-Object { $_.Trim() }) -contains $Category)

      $savedPath = $null
      $status = "ok"
      $note = ""

      if ($mdAttachment) {
        $savedName = "{0}_daily_work_log_{1}.md" -f $matchedName, $TargetDate
        $savedPath = Join-Path $dayDir $savedName
        $mdAttachment.SaveAsFile($savedPath) | Out-Null
      } else {
        $status = "no_attachment"
        $note = "Email matched by subject only; no daily_work_log_*.md attachment"
      }

      if (-not $alreadyProcessed) {
        if ([string]::IsNullOrEmpty($item.Categories)) {
          $item.Categories = $Category
        } else {
          $item.Categories = "$($item.Categories),$Category"
        }
        $item.Save()
      }

      $result.results += [ordered]@{
        sender             = $matchedName
        sender_display     = $sender
        subject            = $subject
        sent_on            = $item.SentOn.ToString("yyyy-MM-dd HH:mm:ss")
        received           = $item.ReceivedTime.ToString("yyyy-MM-dd HH:mm:ss")
        target_date_source = $targetSource
        attachment_saved   = $savedPath
        attachment_name    = if ($mdAttachment) { [string]$mdAttachment.FileName } else { $null }
        category_added     = (-not $alreadyProcessed)
        already_processed  = $alreadyProcessed
        status             = $status
        note               = $note
      }
    } catch {
      $result.errors += [ordered]@{
        stage   = "per_item"
        subject = if ($item) { [string]$item.Subject } else { $null }
        error   = $_.Exception.Message
      }
    }
  }

  foreach ($name in $members) {
    if (-not $seenPerSender.ContainsKey($name)) {
      $result.missing += $name
    }
  }
} catch {
  $result.errors += [ordered]@{
    stage = "setup"
    error = $_.Exception.Message
  }
}

$result | ConvertTo-Json -Depth 6
