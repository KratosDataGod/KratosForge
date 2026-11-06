CREATE OR REPLACE MACRO can_see(who, row_region) AS
    EXISTS (SELECT 1 FROM user_regions AS m
            WHERE m.user_name = lower(who) AND (m.region = row_region OR m.region = '*'))
    OR NOT EXISTS (SELECT 1 FROM user_regions AS m WHERE m.user_name = lower(who));

-- Don't change this line: it shows what each person who signs in would see.
SELECT * FROM what_each_user_sees ORDER BY user_name, sale_id;
