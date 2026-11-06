-- Sales by region, who may see which region, and the people who sign in. All made up, the same every time.
CREATE TABLE sales AS
SELECT i AS sale_id,
       ['North', 'South', 'East', 'West'][1 + i % 4] AS region,
       ['Bikes', 'Helmets', 'Locks'][1 + i % 3] AS product,
       CAST(10 + (i * 37) % 290 AS DECIMAL(10,2)) AS amount
FROM range(1, 25) AS t(i);

-- Stored in lower case. A region of '*' means every region.
CREATE TABLE user_regions (user_name VARCHAR, region VARCHAR);
INSERT INTO user_regions VALUES
    ('ana@example.com', 'North'),
    ('ben@example.com', 'South'),
    ('ben@example.com', 'East'),
    ('cleo@example.com', '*');

-- The names as sign-in returns them: not always in lower case, and not everyone has a mapping.
CREATE TABLE signins (user_name VARCHAR);
INSERT INTO signins VALUES ('Ana@Example.com'), ('ben@example.com'), ('cleo@example.com'), ('dev@example.com');

-- The security predicate: may this person see a row of this region? Right now it lets everyone see everything.
CREATE MACRO can_see(who, row_region) AS true;

-- What each person who signs in would see once the predicate filters the table.
CREATE VIEW what_each_user_sees AS
SELECT s.user_name, x.sale_id, x.region
FROM signins AS s
JOIN sales AS x ON can_see(s.user_name, x.region);
