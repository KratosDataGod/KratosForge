SELECT item, 'add' AS change
FROM dev_items
WHERE item NOT IN (SELECT item FROM prod_items)
ORDER BY item;
