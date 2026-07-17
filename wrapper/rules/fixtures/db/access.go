package widget

func writeWidgets(db *gorm.DB) error {
	return db.Table(constant.TbWidget.String()).
		Where("id = ?", 1).
		Updates(map[string]any{"active": true}).Error   // WRITE widgets (via registry)
}

func readGadgets(db *gorm.DB, out *[]Gadget) error {
	return db.Table(constant.TbGadget.String()).Find(out).Error   // READ gadgets (via registry)
}

func dynamicAccess(db *gorm.DB, name string, out *[]any) error {
	return db.Table(name).Find(out).Error   // UNRESOLVED (dynamic table expression)
}
