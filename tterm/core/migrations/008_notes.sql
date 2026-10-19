-- 0.1.0: notes, in folders, in each project.
--
-- Notes live in folders the way terminals live on machines, and a folder
-- belongs to one project: everyone in the project reads and writes its
-- notes. A note is a title and a list of blocks (a paragraph, a heading, a
-- list item, a checklist item, code, a divider), kept as JSON and always
-- shown as text, never as HTML. See core/notes.py.

CREATE TABLE note_folders (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    created_by  uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Two folders of one name in a project would only confuse.
CREATE UNIQUE INDEX note_folders_name ON note_folders (project_id, lower(name));

CREATE TABLE notes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    folder_id   uuid NOT NULL REFERENCES note_folders (id) ON DELETE CASCADE,
    title       text NOT NULL DEFAULT '' CHECK (length(title) <= 200),
    body        jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(body) = 'array'),
    -- What the sidebar and the project's page show without the body: the
    -- checklist items and how many are done, and the start of the text.
    todo_total  integer NOT NULL DEFAULT 0,
    todo_done   integer NOT NULL DEFAULT 0,
    excerpt     text NOT NULL DEFAULT '',
    -- One more with every change. A save names the version it was made
    -- from; one from an older version is merged with what came since.
    version     integer NOT NULL DEFAULT 1,
    created_by  uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    edited_by   uuid REFERENCES users (id) ON DELETE SET NULL,
    edited_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notes_folder ON notes (folder_id, created_at DESC);

-- The latest versions of each note, the base for merging a save made from
-- an older one. The core keeps a few dozen and drops the rest.
CREATE TABLE note_versions (
    note_id     uuid NOT NULL REFERENCES notes (id) ON DELETE CASCADE,
    version     integer NOT NULL,
    title       text NOT NULL,
    body        jsonb NOT NULL,
    PRIMARY KEY (note_id, version)
);
