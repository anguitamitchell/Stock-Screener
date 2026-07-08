-- Company entries written by the manual-edit and promote-from-review-queue
-- flows always carry a research_status ("curated"); the seed JSON's curated
-- companies omit it and fall back to "curated" at read time in screen_holding().
alter table semantic_companies
    add column if not exists research_status text;
