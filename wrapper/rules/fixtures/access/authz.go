package widget

type WidgetRole int   // role catalog

func setup(r Router, mw Handler, u User, widget Widget, userID int) {
	r.Use(mw)                                  // middleware attach
	if !acl.CheckPermission(u, "read") {       // authz check
		return
	}
	if widget.OwnerID == userID {              // contextual-identity check
		return
	}
}
