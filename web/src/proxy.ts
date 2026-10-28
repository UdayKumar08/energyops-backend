import { NextResponse, type NextRequest } from "next/server";

// A quick bounce to the sign-in page when there is no session cookie at all. It is only a
// convenience: every page and every API call is checked again with the backend, which is what
// actually decides who may see what.
export function proxy(request: NextRequest) {
  if (!request.cookies.has("eo_session")) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|login|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
