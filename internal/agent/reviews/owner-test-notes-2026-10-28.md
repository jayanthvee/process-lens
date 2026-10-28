# Owner test notes 2026-10-28 (for the bunched ticket after PL-121)

- Sales: owner typed the product name into the search. A real employee may just click the product
  card in the list instead (no search). The update must still work, and the row's product must be
  found by its name from the sheet, not by the card's position.
- Seen mid-teach (screenshot): after 1 step, the panel already says "The Click button click saves
  your work", and the name box says "New lead", an old task's name. PL-118 item 5 said it should
  start empty or as "<site> task".
- Oak Standing Desk is already at 910 (not the seeded 749).
- BLOCKER (screenshot 23:51): opening the product form recorded 13 junk "Typed" steps the person
  never did: fill input "on" x2, goods/service/combo "on", track inventory "on", sales price
  "910.00" (the old value), cost "455.00", category "All", reference "PL-DESK-1001", then the real
  sales price "400". The radios read as typed "on". Replaying would overwrite every product's
  cost, category and reference with the Oak desk's. PL-113 was meant to stop junk fills; it hasn't
  for this form. Odoo had already autosaved 910 -> 400 before leaving (chatter).
- Price task (saved ~23:55): compiled steps navigate, fill search, click, branch(no search found),
  click-2 (open card), fill-2 sales_price, click-3..6, then `end-record-unchecked`. So "saves by
  itself" built NO commit and no read-back: every row would park. Find why _self_save_commit
  refused this real recording (leave = the Odoo back arrow; 4 clicks after the fill).
- Try one row: work window went black (about:blank shown) after typing the search, and the row
  ended "No search called 'Oak Standing Desk' was found" while the owner's own tab showed it.
- Check step: Sales price matched to "New Price" but still says "Which column has the sales price
  amounts?".
- Price run 27a0adc9 cause: branch-1 is `element_absent` role=cell name="{{row.search | lower}}".
  The owner's list was the kanban view (cards, no cells), so every row read "No search ... found"
  in 6 s. The black work window was not the cause.
- Self-save refused: the steps after the fill are stray clicks (All Apps, the tab strip, Close
  menu) and the back button (`button.o_back_button`). The leave was not recognised, so
  `end-record-unchecked` was compiled.
