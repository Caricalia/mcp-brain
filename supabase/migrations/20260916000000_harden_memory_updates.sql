-- Keep a memory's author and project stable after creation.
--
-- The Edge Function never accepts either field on update, but authenticated
-- PostgREST clients can update rows directly when the OAuth/RLS path is used.
-- Without this trigger a user who can edit a shared memory could reassign it
-- to themselves and then make it private. The trigger keeps the database
-- invariant true for every write path, including future clients.

create or replace function protect_memory_identity()
returns trigger
language plpgsql
set search_path = public, extensions, pg_temp
as $$
begin
  if new.created_by is distinct from old.created_by then
    raise exception 'Memory author cannot be changed';
  end if;

  if new.project_slug is distinct from old.project_slug then
    raise exception 'Memory project cannot be changed';
  end if;

  return new;
end;
$$;

drop trigger if exists memories_protect_identity_trg on memories;
create trigger memories_protect_identity_trg
  before update on memories
  for each row execute function protect_memory_identity();

-- The old policy's WITH CHECK accepted any row whose new created_by matched
-- the caller, even when the caller was not the original author. The trigger
-- above makes that reassignment impossible; this policy additionally requires
-- the new project to remain visible and permits non-authors to edit shared
-- memories without permitting them to make those memories private.
drop policy if exists memories_update on memories;
create policy memories_update on memories
  for update to authenticated
  using (
    created_by = app_user_id()
    or (
      visibility = 'shared'
      and exists (
        select 1 from projects p
        where p.slug = memories.project_slug
          and (p.owner_id is null or p.owner_id = app_user_id())
      )
    )
  )
  with check (
    exists (
      select 1 from projects p
      where p.slug = memories.project_slug
        and (p.owner_id is null or p.owner_id = app_user_id())
    )
    and (created_by = app_user_id() or visibility = 'shared')
  );
