package widget
func register(r Router) {
	r.GET("/widgets", listWidgets)     // POSITIVE
	r.POST("/gadgets", createGadget)   // POSITIVE
	r.Group("/mounted")                // NEGATIVE (group/mount)
	logger.Print("startup")            // NEGATIVE
}
