# Shortcuts and OneLake security

A shortcut is a pointer: a folder or table in one lakehouse that shows data stored somewhere
else, in another lakehouse, workspace, or in ADLS, S3 or Google Cloud Storage. Nothing is copied,
so there's one copy to keep right, and readers always see the source as it is now.

## Who can read through a shortcut

Access is decided where the data lives. A OneLake shortcut checks the reader's permissions on the
target item; an external shortcut uses the connection's credentials, so everyone who can read the
shortcut reads with them. Give external connections only the access every reader should have.

## Row-level rules

OneLake security, Warehouse security policies and Power BI roles can all filter rows by who is
asking. Underneath, each is a filter like this one, applied for you:

```sql
SELECT s.region, s.amount
FROM sales AS s
WHERE EXISTS (SELECT 1 FROM user_regions AS u
              WHERE u.user_name = USER_NAME() AND u.region = s.region);
```

Keep the mapping in a table owned by the data's owners, so granting a region is a row, not a code
change. The lab names the user, because DuckDB has no signed-in user.

## Try it

Return what ana@contoso.com may see.
