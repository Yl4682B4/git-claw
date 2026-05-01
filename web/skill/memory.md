# Skill: Memory System Exploration

You have access to a **memory directory** that stores structured knowledge and context.

## Memory Directory Location

The memory directory is a sibling of your workspace directory. If your workspace is at `/path/to/web/workspace/`, then memory is at `/path/to/web/memory/`.

## How to Explore Memory

Follow this layered approach to efficiently retrieve relevant information:

### Step 1: Explore the structure

Use `tree` or `ls` to get an overview of the memory directory structure:

```bash
tree memory/ -L 2
```

This gives you a high-level view of how memories are organized (by topic, project, etc.).

### Step 2: Read `brief.md` summaries

Each directory may contain a `brief.md` file — a concise summary of everything in that directory (including subdirectories). Start here to understand what's available without reading every file:

```bash
cat memory/<topic>/brief.md
```

The `brief.md` files are auto-generated summaries. They tell you:
- What knowledge is stored in that directory
- Key points from each file and subdirectory
- Whether you need to dig deeper

### Step 3: Read original files when needed

If the `brief.md` indicates relevant detailed information exists, read the specific source file:

```bash
cat memory/<topic>/<specific-file>.md
```

## Best Practices

1. **Start broad, go deep** — Always check `brief.md` before reading individual files.
2. **Use tree first** — Understand the directory layout before diving in.
3. **Don't read everything** — Only read source files when the brief suggests they contain what you need.
4. **Memory is read-only during inference** — You explore memory to inform your responses, but don't modify it during a conversation.

## When to Use Memory

- When the user asks about something that might have been previously stored
- When you need project-specific context or conventions
- When you need to recall decisions, architecture notes, or domain knowledge
- At the start of complex tasks, to check if relevant context exists
