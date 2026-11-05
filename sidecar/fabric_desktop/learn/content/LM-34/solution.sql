SELECT d.item, CASE WHEN p.item IS NULL THEN 'add' ELSE 'update' END AS change
FROM dev_items AS d
LEFT JOIN prod_items AS p ON p.item = d.item
WHERE p.item IS NULL OR p.version <> d.version
ORDER BY d.item;
