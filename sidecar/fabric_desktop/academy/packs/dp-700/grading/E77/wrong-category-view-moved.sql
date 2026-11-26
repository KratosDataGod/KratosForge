-- delete: dbo/Views/vw_SalesByCategory.sql
-- file: reporting/Views/vw_SalesByCategory.sql
CREATE VIEW [reporting].[vw_SalesByCategory]
AS
SELECT p.[Category], SUM(s.[Amount]) AS [Sales]
FROM [dbo].[FactSales] AS s
JOIN [dbo].[DimProduct] AS p ON p.[ProductKey] = s.[ProductKey]
GROUP BY p.[Category];
-- file: dbo/Tables/FactSales.sql
CREATE TABLE [dbo].[FactSales]
(
    [SaleKey] INT NOT NULL,
    [ProductKey] INT NOT NULL,
    [SaleDate] DATE NOT NULL,
    [Amount] DECIMAL(18, 2) NULL,
    [Channel] VARCHAR(10) NULL
);
-- file: Security/reporting.sql
CREATE SCHEMA [reporting];
-- file: reporting/Views/vw_SalesByChannel.sql
CREATE VIEW [reporting].[vw_SalesByChannel]
AS
SELECT COALESCE(s.[Channel], 'Unknown') AS [Channel], SUM(s.[Amount]) AS [Sales], COUNT(*) AS [Orders]
FROM [dbo].[FactSales] AS s
GROUP BY COALESCE(s.[Channel], 'Unknown');
