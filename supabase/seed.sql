-- Local dev seed. `dev@example.com` is a ready-to-use admin for a clean
-- clone (see CONTRIBUTING.md's quickstart) - issue yourself a token with:
--   select issue_api_key('dev@example.com', 'local');
--
-- IMPORTANT for a real deployment: remove the `dev@example.com` row below
-- and replace the `<email-...>` placeholders with your team's real emails
-- BEFORE running this seed against production (see DEPLOY.md) - don't ship
-- the example team, or the local dev account, as-is.
insert into users (email, name, is_admin) values
  ('dev@example.com',  'Dev Admin', true),
  ('<email-admin>',    'Admin',     true),
  ('<email-teammate>', 'Teammate',  false)
on conflict (email) do nothing;

-- Example team projects
insert into projects (slug, parent_slug, owner_id, name, description) values
  ('acme',      null,   null, 'Acme',       'Example company. Product decisions, pricing, customers, positioning'),
  ('acme-app',  'acme', null, 'Acme App',   'Application repo (backend + frontend)'),
  ('acme-docs', 'acme', null, 'Acme Docs',  'Public documentation repo'),
  ('acme-site', 'acme', null, 'Acme Site',  'Marketing website/landing repo')
on conflict (slug) do nothing;

-- Admin's personal project
insert into projects (slug, parent_slug, owner_id, name, description) values
  ('personal', null, (select id from users where email = '<email-admin>'), 'Personal', 'Admin''s personal project, separate from Acme.')
on conflict (slug) do nothing;

-- dev@example.com's own personal project, so the quickstart has something
-- to try create_project/personal-visibility with out of the box.
insert into projects (slug, parent_slug, owner_id, name, description) values
  ('dev-personal', null, (select id from users where email = 'dev@example.com'), 'Dev Personal', 'Local dev admin''s personal project.')
on conflict (slug) do nothing;
