-- Migration 007: Add task_number serial column for human-readable task IDs
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS task_number SERIAL;
