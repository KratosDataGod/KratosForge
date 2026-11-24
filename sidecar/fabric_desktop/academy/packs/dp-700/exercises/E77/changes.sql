-- E77: sales now record the channel they came through. Ship the change as one change set:
--   1. dbo.FactSales gains a Channel column, VARCHAR(10). Sales loaded before today have no channel.
--   2. A new schema, reporting, for report views. A schema's file is Security/<schema>.sql.
--   3. reporting.vw_SalesByChannel with Channel, Sales (the sum of Amount) and Orders (the number of sales).
--      A sale with no channel is reported as 'Unknown'.
-- Reports still read dbo.vw_SalesByCategory. Each object goes in its own file section, such as
-- -- file: dbo/Tables/FactSales.sql

