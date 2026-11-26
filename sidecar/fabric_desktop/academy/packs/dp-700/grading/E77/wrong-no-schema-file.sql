-- file: dbo/Tables/FactSales.sql
CREATE TABLE [dbo].[FactSales]
(
    [SaleKey] INT NOT NULL,
    [ProductKey] INT NOT NULL,
    [SaleDate] DATE NOT NULL,
    [Amount] DECIMAL(18, 2) NULL,
    [Channel] VARCHAR(10) NULL
);
-- file: reporting/Views/vw_SalesByChannel.sql
CREATE VIEW [reporting].[vw_SalesByChannel]
AS
SELECT COALESCE(s.[Channel], 'Unknown') AS [Channel], SUM(s.[Amount]) AS [Sales], COUNT(*) AS [Orders]
FROM [dbo].[FactSales] AS s
GROUP BY COALESCE(s.[Channel], 'Unknown');
