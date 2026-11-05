CREATE TABLE dev_items (item VARCHAR, version INTEGER);
INSERT INTO dev_items VALUES ('nb_load', 3), ('nb_clean', 2), ('model', 5), ('report_new', 1);
CREATE TABLE prod_items (item VARCHAR, version INTEGER);
INSERT INTO prod_items VALUES ('nb_load', 3), ('nb_clean', 1), ('model', 5), ('old_report', 4);
