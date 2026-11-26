CREATE VIEW [dbo].[vw_SalesByCategory]
AS
SELECT p.[Category], SUM(s.[Amount]) AS [Sales]
FROM [dbo].[FactSales] AS s
JOIN [dbo].[DimProduct] AS p ON p.[ProductKey] = s.[ProductKey]
GROUP BY p.[Category];
