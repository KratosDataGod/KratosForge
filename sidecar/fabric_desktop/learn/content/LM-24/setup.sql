CREATE TABLE sales (region VARCHAR, amount DECIMAL(10, 2));
INSERT INTO sales VALUES ('North', 10.00), ('North', 30.00), ('South', 20.00), ('West', 5.00), ('East', 8.00);
CREATE TABLE user_regions (user_name VARCHAR, region VARCHAR);
INSERT INTO user_regions VALUES
    ('ana@contoso.com', 'North'), ('ana@contoso.com', 'West'), ('ben@contoso.com', 'South');
