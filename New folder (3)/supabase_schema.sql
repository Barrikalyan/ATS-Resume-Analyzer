create table if not exists public.applications (
    id text primary key,
    name text,
    email text,
    role text,
    score double precision,
    status text,
    resume_path text,
    data jsonb not null
);

alter table public.applications enable row level security;

create table if not exists public.quiz_sessions (
    session_id text primary key,
    data jsonb not null
);

alter table public.quiz_sessions enable row level security;

insert into storage.buckets (id, name, public)
values ('resumes', 'resumes', false)
on conflict (id) do update set public = false;

insert into storage.buckets (id, name, public)
values ('interview-assets', 'interview-assets', false)
on conflict (id) do update set public = false;
