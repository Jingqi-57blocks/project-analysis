package widget
import "net/http"
func fetchThings(u string) {
	req, _ := http.NewRequest("GET", u, nil)  // POSITIVE
	http.Get(u)                                // POSITIVE
	computeLocally()                           // NEGATIVE
	_ = req
}
