package widget
func setup() {
	c := NewGadgetClient()   // POSITIVE
	s := NewSession()        // POSITIVE
	x := computeLocally()    // NEGATIVE
	_ = c; _ = s; _ = x
}
