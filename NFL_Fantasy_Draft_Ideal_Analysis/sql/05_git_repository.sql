-- 05_git_repository.sql — mount this GitHub repo inside Snowflake so Snowsight
-- browses and runs the sql/ files directly, in the same tree as GitHub.
-- Optional: nothing downstream depends on it. Executed against Snowflake.
--
-- After this runs, Snowsight → Projects → Workspaces → "From Git repository"
-- shows NFL_Fantasy_Draft_Ideal_Analysis/sql/*.sql as editable, runnable files;
-- each one opens as a worksheet whose path matches the repo path exactly.

USE DATABASE FANTASY;

-- The repo is public, so no SECRET / ALLOWED_AUTHENTICATION_SECRETS is needed.
-- Add one here if the repo is ever made private.
CREATE API INTEGRATION IF NOT EXISTS GITHUB_AB182024W
    API_PROVIDER = GIT_HTTPS_API
    API_ALLOWED_PREFIXES = ('https://github.com/ab182024w-sketch')
    ENABLED = TRUE
    COMMENT = 'Read-only access to this owner''s public GitHub repos.';

CREATE SCHEMA IF NOT EXISTS REPO
    COMMENT = 'Git-backed mirror of the GitHub repo; holds no data.';

CREATE GIT REPOSITORY IF NOT EXISTS REPO.AB_PRJCTS_2026
    API_INTEGRATION = GITHUB_AB182024W
    ORIGIN = 'https://github.com/ab182024w-sketch/AB-PRJCTS-2026.git';

-- Snapshot, not a live view: re-run FETCH after every push, or the workspace
-- keeps serving the commit it last pulled.
ALTER GIT REPOSITORY REPO.AB_PRJCTS_2026 FETCH;

-- Branch names containing '/' must be double-quoted inside the stage path.
LS @REPO.AB_PRJCTS_2026/branches/main/NFL_Fantasy_Draft_Ideal_Analysis/sql/;

-- Files can also be executed straight from the repo, no copy/paste:
--   EXECUTE IMMEDIATE FROM
--     @REPO.AB_PRJCTS_2026/branches/main/NFL_Fantasy_Draft_Ideal_Analysis/sql/20_staging.sql;
-- Note this runs the whole file as one script, so the ad-hoc SELECTs in
-- 10_raw.sql and 99_tests.sql return nothing visible — for those, open the file
-- in the workspace and run statements interactively.
