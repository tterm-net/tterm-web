-- 0.1.1: projects of one's own. Anyone makes more of them, renames them,
-- Personal too, and deletes one that has no machines; a machine moves from
-- one to another. Each project has a color, and a terminal a name of its
-- own. See core/projects.py.

-- The color marks the project in the sidebar and in tabs, for everyone in
-- it: the page knows these eight by name.
ALTER TABLE projects ADD COLUMN color text NOT NULL DEFAULT 'violet'
    CHECK (color IN ('violet', 'sky', 'teal', 'pink', 'orange', 'lime',
                     'indigo', 'slate'));
-- Projects made before: each owner's take the colors in turn, oldest first.
UPDATE projects p
SET color = (ARRAY['violet', 'sky', 'teal', 'pink', 'orange', 'lime',
                   'indigo', 'slate'])[1 + (r.n - 1) % 8]
FROM (SELECT id, row_number() OVER (PARTITION BY owner_id
                                    ORDER BY created_at, id) AS n
      FROM projects) r
WHERE r.id = p.id;

-- A deleted project stays in the database, so that the journal and the
-- history of its old machines keep what they point to, but nobody is in it
-- any more and nothing shows it. Its code is free again.
ALTER TABLE projects ADD COLUMN deleted_at timestamptz;
ALTER TABLE projects DROP CONSTRAINT projects_owner_id_code_key;
CREATE UNIQUE INDEX projects_code ON projects (owner_id, code)
    WHERE deleted_at IS NULL;
-- What the constraint's index did besides: each owner's projects, the
-- first of them (Personal) first, deleted ones too.
CREATE INDEX projects_owner ON projects (owner_id, created_at, id);

-- How much of a project's color this person's sidebar shows: its badge
-- only, or the whole block of the project.
ALTER TABLE users ADD COLUMN project_tint text NOT NULL DEFAULT 'badge'
    CHECK (project_tint IN ('badge', 'block'));

-- A terminal's name, as its owner gives it; until then the number it was
-- opened with, as before.
ALTER TABLE terminals ADD CONSTRAINT terminals_name_length
    CHECK (name IS NULL OR length(name) BETWEEN 1 AND 40);
