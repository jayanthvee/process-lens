-- The mapping a person confirmed for one uploaded spreadsheet: one entry per task column, naming
-- the spreadsheet column it came from. It lives on the batch, so the recipe is never mutated.
ALTER TABLE input_batches ADD COLUMN column_mapping jsonb;
