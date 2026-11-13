-- Write the security predicate. who is the name as sign-in returns it; row_region is the row's region.
-- Return true only when user_regions lets this person see that region.
CREATE OR REPLACE MACRO can_see(who, row_region) AS
    true;

-- Don't change this line: it shows what each person who signs in would see.
SELECT * FROM what_each_user_sees ORDER BY user_name, sale_id;
