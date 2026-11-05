# NULL: the value that isn't there

NULL means "no value": not zero, not an empty string. SQL compares it with three answers instead
of two: true, false and unknown. `WHERE` keeps only the rows where the condition is true, so a
comparison that comes out unknown quietly drops the row.

## IS NULL, never = NULL

`closed_at = NULL` is unknown for every row, even the empty ones, so it keeps nothing. Ask with
`IS NULL` or `IS NOT NULL`:

```sql
SELECT ticket_id FROM tickets WHERE closed_at IS NULL;
```

## A comparison drops the empty rows

`priority <> 'low'` is unknown when priority is empty, so tickets with no priority vanish. When
they belong in the answer, say so:

```sql
WHERE priority IS DISTINCT FROM 'low'          -- true for an empty priority
WHERE coalesce(priority, 'none') <> 'low'      -- the same, with a stand-in value
```

The same thing happens in joins and in `NOT IN`: the anti-joins lesson (LM-17) shows the trap.
`count(column)` and `avg(column)` skip empty values too, while `count(*)` counts every row.

## Try it

List the open tickets that aren't low priority, including those with no priority set.
