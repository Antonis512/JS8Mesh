# PDF Manual Plan

## Goal

Create a user-friendly PDF manual for GitHub Releases and normal end users.

The Markdown version remains the editable source:

- [USER_MANUAL.md](USER_MANUAL.md)

The PDF version is the release-friendly document:

- `USER_MANUAL.pdf`

## Recommended Workflow

1. Keep `USER_MANUAL.md` as the source of truth.
2. When content is stable, convert it to PDF.
3. Upload the PDF together with the `.exe` in GitHub Releases.

## Suggested PDF Contents

The PDF should contain:

1. Title page
   - `JS8Mesh v0.9.0-beta`
   - subtitle
   - short disclaimer

2. Short introduction
   - what JS8Mesh is
   - what it is not

3. Install / first run

4. Main window overview

5. Settings

6. Mesh Reports and request types
   - `JR`
   - `JRN`
   - `JRS`
   - `HR`
   - `HRC`

7. Relay Message Builder

8. Topology and Loggers

9. Legal / responsibility disclaimer

10. Credits and license

## Recommended Screenshots

Add screenshots for:

- main window
- `Request JR/HR`
- `Requested JR`
- `TX Mesh Reports`
- `Topology`
- one logger window

## Simple Conversion Options

### Option 1: Browser Print to PDF

1. Open `USER_MANUAL.md` rendered in GitHub or a Markdown viewer.
2. Print to PDF.

Pros:
- simple
- no extra tooling

Cons:
- layout control is limited

### Option 2: Word / LibreOffice

1. Copy the manual into Word or LibreOffice.
2. clean page breaks and headings
3. insert screenshots
4. export to PDF

Pros:
- easiest polished result
- good control over spacing and screenshots

Cons:
- manual step

### Option 3: Markdown-to-PDF Tooling

Use a converter such as Pandoc later if you want a repeatable build workflow.

Pros:
- repeatable
- scriptable

Cons:
- extra dependency/tooling

## Best Practical Recommendation

For the first public release:

- keep `USER_MANUAL.md` in the repo
- make a polished `USER_MANUAL.pdf` manually from Word or LibreOffice
- attach the PDF to the GitHub Release

This is the fastest path to a clean user-facing result.
