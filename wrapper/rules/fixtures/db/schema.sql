CREATE TABLE widgets (
  id INT PRIMARY KEY,
  gadget_id INT,
  FOREIGN KEY (gadget_id) REFERENCES gadgets(id)
);
INSERT INTO widgets (id) VALUES (1);
UPDATE gadgets SET name = 'x' WHERE id = 1;
SELECT * FROM sprockets JOIN widgets ON sprockets.widget_id = widgets.id;
