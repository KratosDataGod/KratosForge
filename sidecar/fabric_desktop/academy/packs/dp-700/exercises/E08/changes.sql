-- E08: add dbo.DimStore and dbo.vw_SalesByRegion to the warehouse project.
-- Each object goes in its own file section: -- file: dbo/Tables/<Name>.sql or dbo/Views/<Name>.sql
-- A teammate's change is already in this change set, below. Check what it does before you ship it.

-- file: dbo/Tables/DimProduct.sql
-- A teammate's change, waiting to go in with yours: Category moves to its own table later.
CREATE TABLE [dbo].[DimProduct]
(
    [ProductKey] INT NOT NULL,
    [ProductName] VARCHAR(100) NULL
);
