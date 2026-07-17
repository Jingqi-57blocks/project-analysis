package widget

type TableName string

const (
	TbWidget TableName = "widgets"    // registry entry (literal)
	TbGadget TableName = "gadgets"    // registry entry (literal)
	TbAlias  TableName = OtherConst   // non-literal → unresolved go-const
)

func (w *Widget) TableName() string { return "widgets" }                   // literal declaration
func (g *Gadget) TableName() string { return constant.TbGadget.String() }  // resolves via registry
func (s *Sprocket) TableName() string { return dynamicName() }             // unresolved binding
