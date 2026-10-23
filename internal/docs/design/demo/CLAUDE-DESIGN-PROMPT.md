# Prompt for Claude Design: ProcessLens end-to-end clickable demo

**Attach these 3 files to the prompt:**
- `internal/docs/design/home-mock.html`: the user dashboard (light);
- `internal/docs/design/admin-mock.html`: the admin dashboard (dark);
- `internal/docs/design/demo/customer-inquiries.csv`: the demo spreadsheet (20 rows).

Paste everything below the line.

---

Build a **clickable, end-to-end prototype** of ProcessLens for a live presentation tomorrow. It must run in the
browser with no backend: every screen is real HTML, every step is clickable, and all data is the attached CSV.

## What ProcessLens is
ProcessLens is an internal tool that learns a repetitive data-entry task by watching a person do it once in
their browser (through a Chrome extension). It then repeats the task for every row of a spreadsheet, safely:
- it never enters the same thing twice;
- it stops and asks a person when something is unclear;
- it shows how much time it saved.

The users are **not technical**. Every screen has one obvious next step.

## The two dashboards: copy them exactly, do not redesign
- **The user dashboard (Home)** is `home-mock.html`. Rebuild it **pixel-for-pixel**:
  - same tokens, Public Sans, sidebar, header, headline, the gold "Time saved" figure, the panels and the table;
  - keep its live progress animation.
- **The admin dashboard (Overview)** is `admin-mock.html`. Rebuild it **pixel-for-pixel**: the same dark fog,
  sidebar and figure strip, the "Needs a look" and "Running now" panels, and the working "Pause everything"
  confirmation and frozen bar.
- Every new screen you add **reuses their exact tokens and components**:
  - frosted glass panels;
  - hairline rows, each with a title, one line of explanation and one quiet button;
  - status shown as a dot plus a word;
  - the gold gradient primary button.

## Design rules (strict)
- **Font:** Public Sans only, weights 300–600. No monospace. No uppercase labels or letter-spaced eyebrows.
- **The user app is light only;** the admin app is dark only. Gold is the only accent.
- **One primary (gold) button per screen.**
- **Banned:**
  - the fonts Inter, Space Grotesk, Manrope, Playfair, Lora and Schibsted;
  - warm paper or cream backgrounds;
  - purple or blue gradients;
  - pill chips and tag clouds;
  - card grids;
  - Tailwind-default look;
  - emoji.
- **Words on screen.** Never show engine words like recipe, record, parked, verified, selector, JSON or step id.
  Use these instead:

  | Engine word | On screen |
  |---|---|
  | recipe | task |
  | parked | needs your help |
  | duplicate / skipped | already there |
  | failed | didn't work, plus the reason |
  | value mapping | saved answer |

- **Motion:** sections blur in on first load (850 ms, staggered 70 ms); progress bars ease. Respect
  `prefers-reduced-motion`.

## The use case (from the brief: "Customer inquiry registration")
**Problem:** staff copy customer inquiries from a spreadsheet into the CRM by hand.

**The task:** for each row of `customer-inquiries.csv`, search Odoo CRM by email.
- If the lead exists, it's **already there**.
- If not, create a new lead with name, email, phone, company, "Interested in", the message, and a salesperson.

**Rules the person sets:**
- **Already there:** a lead with the same email exists in Odoo, or earlier in this spreadsheet → skip it.
- **Missing email** → needs your help ("No email, so we can't check whether this person is already in Odoo").
- **Salesperson blank** → assign by region:
  - North → Ravi Iyer;
  - South → Ana Costa;
  - West → Mei Chen.
- **Salesperson not found in Odoo** → needs your help, with a suggestion.

**The expected outcome for the 20 rows** (use exactly these):
- **Already in Odoo before the run:** Marcus Bell and Elena Petrova.
- **Repeated inside the spreadsheet:** Daniel Okafor (row 10) and Sara Lindqvist (row 18).
- So **4 already there.**
- **No email:** Aiko Tanaka and Omar Haddad. These **need your help**; the person types an email or chooses
  "Skip this row".
- **Priya Nair** has salesperson "R. Iyer", which isn't in Odoo. Suggest "Ravi Iyer?". Accepting saves it as a
  saved answer ("R. Iyer" → "Ravi Iyer") for future runs.
- **Everyone else is added (13).** After Priya is fixed, **14 added.**
- **Time:**
  - by hand, about 60 minutes (20 rows × 3 minutes);
  - ProcessLens took 4 min 10 s;
  - so **"About 56 minutes saved"**, in the gold figure.

## The flow: one continuous story, start to finish
A small "Demo" step indicator (plain text, bottom-left, for example "3 of 9 · Teach") lets the presenter jump
straight to any step, including **Admin**. The user app and the admin panel are two separate apps in one
prototype: switching between them changes the whole look (light to dark).
The arrow keys move forward and back.

1. **Home (the user dashboard),** exactly as `home-mock.html`. The presenter clicks **Teach a new task**.
2. **Teach, part 1: the spreadsheet and the app.**
   - "Which spreadsheet?": drop zone, then `customer-inquiries.csv` is loaded, showing "20 rows · 8 columns" and
     a preview of the first 5 rows in a clean table.
   - "Which app?": **Odoo CRM** is selected, with the "Chrome connected" status.
   - Primary button: **Start teaching**.
3. **Teach, part 2: recording, in a fake browser with the extension working.** This is the centrepiece.
   - Show a realistic Chrome window (tabs, address bar `odoo.northwind.local/crm`) containing a **simplified
     Odoo-style CRM**:
     - a leads list, a **New** button, and a "New lead" form with the fields Contact name, Email, Phone,
       Company, Interested in, Message, and Salesperson (a dropdown);
     - a **Save** button and a "Lead created" toast.
     - It's a neutral generic CRM look; don't copy Odoo's logo or branding.
   - **The ProcessLens extension popup** is open at the top right, under a puzzle-piece toolbar icon. It's
     ~360 px wide, in the **same light design language** (Public Sans, fog, gold primary). From top to bottom:
     - "Connected to ProcessLens";
     - a large **Stop teaching** button with a pulsing red "Recording" dot;
     - the **live list of recorded steps**, in plain sentences.
   - **Auto-play the person doing row 1 (Priya Nair):**
     - the cursor clicks New;
     - text types into each field, character by character;
     - the salesperson dropdown opens and Ravi Iyer is picked;
     - Save is clicked and the toast appears.
   - **As each action happens, a new step rises into the extension's list:**
     1. Opened Odoo CRM
     2. Clicked New
     3. Typed the contact name
     4. Typed the email
     5. Typed the phone
     6. Typed the company
     7. Chose "Interested in"
     8. Typed the message
     9. Chose the salesperson
     10. Clicked Save
     11. Saw "Lead created"
   - Also show one moment of correction: the person types the email wrong, deletes it, and retypes it. The
     extension shows only the final value, with a small note: "Kept your final text".
   - A **Play / Replay** control.
   - When it finishes, the presenter clicks **Stop teaching**. The popup turns into the **Save as task** form:
     - the task name, prefilled "Add customer inquiries to Odoo CRM";
     - "Which column identifies each row?", set to Email;
     - "Sites this task may use", set to odoo.northwind.local;
     - one gold **Save as task** button.
4. **The task page:** "Add customer inquiries to Odoo CRM".
   - **Steps:** the 11 sentences above, with rename, remove and drag handles.
   - **Spreadsheet columns,** auto-matched because the values typed while teaching equal row 1 of the
     spreadsheet. Each match has a green dot and "matched from your example":
     - Name → Contact name
     - Email → Email
     - Phone → Phone
     - Company → Company
     - Interested in → Interested in
     - Message → Message
     - Salesperson → Salesperson
   - **Rules:** the four rules above, as plain sentences, each with a select:
     - "If it's already there": skip it / update it;
     - "If the email is missing": ask me / skip the row;
     - "If the salesperson is blank": assign by region (with an edit link showing the 3 mappings);
     - "If an option isn't in Odoo": ask me.
   - Primary button: **Check my spreadsheet**.
5. **The spreadsheet check** (the brief's "input review"):
   - "17 rows are ready. 3 need a look before we start."
   - **Show only the problem rows,** with inline fixes:
     - Aiko Tanaka, "No email": an input, or "Skip this row";
     - Omar Haddad, "No email": the same;
     - Priya Nair, "'R. Iyer' isn't a salesperson in Odoo": **Use Ravi Iyer** (shows "Saved for next time" after
       the click).
   - Also show, as information: "2 rows repeat earlier rows (Daniel Okafor, Sara Lindqvist). They'll be
     skipped."
   - Have the presenter fix Priya, and leave the two missing emails, to show "needs your help" later.
   - Primary button: **Try one row**.
6. **Try one row.**
   - The fake browser again, with the extension popup showing "Running · row 1 of 1"; the CRM form fills by
     itself, quickly.
   - Before Save, the form pauses, and a ProcessLens approval panel shows **"About to save this lead"**, listing
     the exact values: Priya Nair, priya.nair@brightpath.io, …, Salesperson: Ravi Iyer.
   - Buttons: **Looks right, save it** and **Stop**.
   - After the click: "Lead created", and "Row 1 worked. Run the other 19?"
   - Primary button: **Run the other 19 rows**.
7. **The run page:** live.
   - A progress bar from 1 to 20, with row 1 already done (about 8 seconds in the demo).
   - A results table filling in row by row, each row with its outcome:
     - added (green dot);
     - already there (grey dot), with the reason "Already in Odoo" or "Same email as row 2";
     - needs your help (amber dot).
   - The two missing-email rows appear in a **Needs your help** panel as they happen.
   - Show **Pause** and **Cancel** (quiet buttons).
8. **Results.**
   - The headline: "14 added. 4 were already there. 2 need you."
   - **The time comparison, as the hero:**
     - the gold figure **About 56 minutes saved**;
     - beneath it, two simple horizontal bars to scale: "By hand: about 60 min" and "ProcessLens: 4 min 10 s".
   - The full results table, and **Export results** (a quiet button).
   - The two "needs your help" rows, each with one action: **Add email**, which then shows "Try again".
   - Then **Back to Home**: Home now shows the updated numbers, the run under "My tasks" as "All done", and the
     Needs help count.
9. **The admin panel: a full tour, dark fog, every sidebar page clickable.**
   - From the avatar menu: **Open admin**.
   - **The same rules for every admin page:**
     - **Dark only.** Copy the tokens, sidebar and components from `admin-mock.html`.
     - Red (`--stop`) appears only on Pause everything.
     - The admin never sees row contents (no emails, no messages), only people, tasks, counts and reasons.
   - **The sidebar:** Overview, People, Runs, Tasks, AI spend and Activity. At the bottom: Settings ("Coming
     soon") and **Back to ProcessLens**.

   **Overview** (`admin-mock.html`, exactly).
   - The figures include this run.
   - "Needs a look" also shows "3 people need to update the Chrome extension".
   - Demo **Pause everything**:
     - the confirmation, then the red-outlined frozen bar ("All runs are paused. Paused by Jay at 14:02.");
     - the running runs change to "Pausing";
     - then **Let runs start again**.

   **People.** A table of 9 people, with columns name and email, role, **Chrome extension**, last active, and
   one action:
   - The people: Jay Vee (Owner), Priya Nair, Ravi Iyer, Ana Costa, Mei Chen, Tom Okoro, Lena Fischer, Sam
     Patel and Noor Ali.
   - Extension versions:
     - most are on 1.5.0;
     - Tom, Lena and Sam are on 1.4.2, shown as "Out of date" in amber;
     - Noor shows "Not seen yet".
   - Above the table, a small **Minimum extension version** form, set to 1.5.0. Changing it shows "This would
     stop 3 people from starting new runs until they update" before saving.
   - The role select has one plain line per role:
     - Owner: everything, including this panel;
     - Builder: teaches and runs tasks;
     - Reviewer: fixes rows that need help, and approves;
     - Viewer: can look, can't change anything.
   - **Turn off access**, with an in-page confirmation. Turned-off people show greyed out, with **Turn access
     back on**.
   - In place of an invite button: "People join by signing in with your company account".

   **Runs.** Every run across the company:
   - the columns: task, person, started, status (a dot plus a word), and "14 of 20 done";
   - the top row is our demo run, "All done";
   - two filters only: person and status;
   - **Pause** and **Cancel** on the running ones.

   **Tasks.** Read-only. The columns are task, who taught it, the apps, runs in the last 30 days, and the share of
   rows done. Use the four tasks from Home's "My tasks", plus "Update deal stages in Twenty", marked "Keeps
   failing".

   **AI spend.**
   - This month's total, "$41.20".
   - A per-day gold bar chart for the month, to scale, with an axis.
   - "Where it went": a table with
     - Cleaning spreadsheet values: 58%;
     - Reading documents: 34%;
     - Fixing broken steps: 8%.
   - The model names show only on hover.

   **Activity.** A list of plain sentences, newest first, each with a person and a time:
   - "Jay ran *Add customer inquiries to Odoo CRM*: 14 added, 4 already there, 2 need help";
   - "Jay approved the first save in *Add customer inquiries to Odoo CRM*";
   - "Jay saved 'R. Iyer' as Ravi Iyer";
   - "Jay paused all runs" and "Jay let runs start again", if the presenter demoed it;
   - "Jay set the minimum extension version to 1.5.0";
   - "Jay turned off access for Noor Ali".

   It has a person filter and **Export CSV**.

   **Back to ProcessLens** returns to the light Home.

## Build notes
- One self-contained prototype. Simple state in JavaScript; no external images.
- The fake Chrome and CRM are drawn in HTML/CSS. The cursor is an animated element.
- Every timed animation has a **Skip** control, so the presenter is never stuck waiting.
- **Desktop first, 1440×900.** It must still be readable on a projector: body text at least 14.5 px.
- Use the CSV's real rows and values everywhere. Don't invent extra rows or people.
