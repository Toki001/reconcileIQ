// Static, bounded local HTTP health probe for official distroless monitoring images.
package main
import("net/http";"os";"time")
func main(){if len(os.Args)!=2{os.Exit(1)};c:=http.Client{Timeout:2*time.Second};r,e:=c.Get(os.Args[1]);if e!=nil{os.Exit(1)};defer r.Body.Close();if r.StatusCode!=200{os.Exit(1)}}
