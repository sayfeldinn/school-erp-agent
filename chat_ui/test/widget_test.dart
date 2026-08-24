import 'dart:convert';

import 'package:chat_ui/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _email = 'teacher.ahmed@school-a.edu';
const _password = 'temporary widget test password';
const _token = 'test-token';
const _generatedEmail = 'sara.mohamed@school-a.edu';
const _registrationName = 'Sara Mohamed';
const _schoolCode = 'ANS-A-S';

Future<void> _pumpApp(WidgetTester tester, http.Client client) async {
  await tester.pumpWidget(SchoolERPAgentApp(client: client));
  await tester.pumpAndSettle();
}

Future<void> _signIn(WidgetTester tester) async {
  final fields = find.byType(TextField);
  await tester.enterText(fields.at(0), _email);
  await tester.enterText(fields.at(1), _password);
  await tester.tap(find.text('Sign in'));
  await tester.pumpAndSettle();
}

http.Response _loginSuccess() {
  return http.Response(
    jsonEncode({'access_token': _token, 'token_type': 'bearer'}),
    200,
    headers: {'Content-Type': 'application/json'},
  );
}

Map<String, String> _lowercaseHeaders(http.BaseRequest request) {
  return {
    for (final entry in request.headers.entries)
      entry.key.toLowerCase(): entry.value,
  };
}

Future<void> _openCreateAccount(WidgetTester tester) async {
  await tester.tap(find.text('Create account'));
  await tester.pumpAndSettle();
}

Future<void> _fillRegistrationForm(
  WidgetTester tester, {
  String password = _password,
  String confirmPassword = _password,
}) async {
  final fields = find.byType(TextField);
  await tester.enterText(fields.at(0), _registrationName);
  await tester.enterText(fields.at(1), password);
  await tester.enterText(fields.at(2), confirmPassword);
  await tester.enterText(fields.at(3), _schoolCode);
}

void main() {
  testWidgets('app starts at login', (tester) async {
    final client = MockClient((request) async {
      fail('initial render must not make an HTTP request');
    });

    await _pumpApp(tester, client);

    final fields = tester.widgetList<TextField>(find.byType(TextField)).toList();
    expect(fields, hasLength(2));
    expect(fields.first.keyboardType, TextInputType.emailAddress);
    expect(fields.last.obscureText, isTrue);
    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byIcon(Icons.send), findsNothing);
    expect(find.text('School ERP Assistant'), findsNothing);
  });

  testWidgets('login opens an authority-free create account form and returns', (
    tester,
  ) async {
    final client = MockClient((request) async {
      fail('opening or cancelling registration must not make an HTTP request');
    });

    await _pumpApp(tester, client);

    expect(find.text('Create account'), findsOneWidget);
    expect(find.text('Forgot Password'), findsNothing);
    await _openCreateAccount(tester);

    final fields = tester.widgetList<TextField>(find.byType(TextField)).toList();
    expect(fields, hasLength(4));
    expect(
      fields.map((field) => field.decoration?.labelText).toList(),
      ['Full Name', 'Password', 'Confirm Password', 'School Code'],
    );
    expect(fields.map((field) => field.obscureText).toList(), [
      false,
      true,
      true,
      false,
    ]);
    expect(find.text('Create Account'), findsOneWidget);
    expect(find.text('Email'), findsNothing);
    expect(find.text('Role'), findsNothing);
    expect(find.text('Tenant'), findsNothing);
    expect(find.text('School'), findsNothing);
    expect(find.text('Admin'), findsNothing);
    expect(find.text('Forgot Password'), findsNothing);

    await tester.tap(find.text('Back to Login'));
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.text('Create account'), findsOneWidget);
  });

  testWidgets('registration sends only approved fields and returns server email', (
    tester,
  ) async {
    http.Request? registrationRequest;
    final requestPaths = <String>[];
    final client = MockClient((request) async {
      requestPaths.add(request.url.path);
      expect(request.url.path, '/auth/register');
      registrationRequest = request;
      return http.Response(
        jsonEncode({'status': 'created', 'email': _generatedEmail}),
        201,
        headers: {'Content-Type': 'application/json'},
      );
    });

    await _pumpApp(tester, client);
    await _openCreateAccount(tester);
    await _fillRegistrationForm(tester);
    await tester.tap(find.text('Create Account'));
    await tester.pumpAndSettle();

    expect(registrationRequest, isNotNull);
    expect(registrationRequest!.method, 'POST');
    expect(requestPaths, ['/auth/register']);
    expect(
      jsonDecode(registrationRequest!.body),
      {
        'full_name': _registrationName,
        'password': _password,
        'confirm_password': _password,
        'school_code': _schoolCode,
      },
    );
    final headers = _lowercaseHeaders(registrationRequest!);
    expect(headers['content-type'], contains('application/json'));
    expect(headers.containsKey('authorization'), isFalse);
    expect(headers.containsKey('x-agent-user'), isFalse);
    expect(headers.containsKey('x-agent-role'), isFalse);
    expect(headers.containsKey('x-agent-school'), isFalse);

    expect(find.text('Sign in'), findsOneWidget);
    final loginFields =
        tester.widgetList<TextField>(find.byType(TextField)).toList();
    expect(loginFields, hasLength(2));
    expect(loginFields.first.controller?.text, _generatedEmail);
    expect(loginFields.last.controller?.text, isEmpty);
    expect(
      find.text('Account created. Sign in with $_generatedEmail.'),
      findsOneWidget,
    );
    expect(find.text('School ERP Assistant'), findsNothing);
    expect(find.byIcon(Icons.send), findsNothing);
  });

  testWidgets('registration failure is generic and stays on create account', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/auth/register');
      return http.Response(
        jsonEncode({
          'detail': 'SQL conflict for role admin, tenant school-b, ERP ID 999',
        }),
        400,
        headers: {'Content-Type': 'application/json'},
      );
    });

    await _pumpApp(tester, client);
    await _openCreateAccount(tester);
    await _fillRegistrationForm(tester);
    await tester.tap(find.text('Create Account'));
    await tester.pumpAndSettle();

    expect(find.text('Account creation failed.'), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(4));
    expect(find.textContaining('SQL'), findsNothing);
    expect(find.textContaining('admin'), findsNothing);
    expect(find.textContaining('tenant'), findsNothing);
    expect(find.textContaining('ERP ID'), findsNothing);
    expect(find.text('Sign in'), findsNothing);
  });

  testWidgets('registration validation rejects empty and mismatched passwords', (
    tester,
  ) async {
    var requestCount = 0;
    final client = MockClient((request) async {
      requestCount += 1;
      return http.Response('{}', 500);
    });

    await _pumpApp(tester, client);
    await _openCreateAccount(tester);
    await tester.tap(find.text('Create Account'));
    await tester.pumpAndSettle();

    expect(find.text('Please complete all fields.'), findsOneWidget);
    expect(requestCount, 0);

    await _fillRegistrationForm(
      tester,
      confirmPassword: 'different confirmation password',
    );
    await tester.tap(find.text('Create Account'));
    await tester.pumpAndSettle();

    expect(find.text('Passwords do not match.'), findsOneWidget);
    expect(find.text('Please complete all fields.'), findsNothing);
    expect(requestCount, 0);
    expect(find.byType(TextField), findsNWidgets(4));
  });

  testWidgets('successful login opens chat', (tester) async {
    final client = MockClient((request) async {
      expect(request.method, 'POST');
      expect(request.url.path, '/auth/login');
      return _loginSuccess();
    });

    await _pumpApp(tester, client);
    await _signIn(tester);

    expect(find.text('Sign in'), findsNothing);
    expect(find.text('School ERP Assistant'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    expect(find.byIcon(Icons.send), findsOneWidget);
  });

  testWidgets('login failure stays on login with a generic error', (tester) async {
    final client = MockClient((request) async {
      expect(request.method, 'POST');
      expect(request.url.path, '/auth/login');
      return http.Response(
        jsonEncode({'detail': 'authentication_failed'}),
        401,
        headers: {'Content-Type': 'application/json'},
      );
    });

    await _pumpApp(tester, client);
    await _signIn(tester);

    expect(find.text('Authentication failed.'), findsOneWidget);
    expect(find.text('Unknown email'), findsNothing);
    expect(find.text('Incorrect password'), findsNothing);
    expect(find.text('Tenant not found'), findsNothing);
    expect(find.text('Account disabled'), findsNothing);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.byIcon(Icons.send), findsNothing);
  });

  testWidgets('chat uses bearer and no legacy identity headers', (tester) async {
    http.BaseRequest? chatRequest;
    final client = MockClient((request) async {
      if (request.url.path == '/auth/login') {
        return _loginSuccess();
      }
      if (request.url.path == '/chat') {
        chatRequest = request;
        return http.Response(
          jsonEncode({
            'status': 'answered',
            'answer': 'There are 14 students in Grade 5.',
            'session_id': 'test-session',
            'iterations': 2,
          }),
          200,
          headers: {'Content-Type': 'application/json'},
        );
      }
      return http.Response('{}', 404);
    });

    await _pumpApp(tester, client);
    await _signIn(tester);
    await tester.enterText(find.byType(TextField), 'How many Grade 5 students?');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(chatRequest, isNotNull);
    expect(chatRequest!.method, 'POST');
    final headers = _lowercaseHeaders(chatRequest!);
    expect(headers['authorization'], 'Bearer $_token');
    expect(headers['content-type'], contains('application/json'));
    expect(headers.containsKey('x-agent-school'), isFalse);
    expect(headers.containsKey('x-agent-role'), isFalse);
    expect(headers.containsKey('x-agent-user'), isFalse);
    expect(find.text('There are 14 students in Grade 5.'), findsOneWidget);
  });

  testWidgets('chat 401 returns to login', (tester) async {
    final client = MockClient((request) async {
      if (request.url.path == '/auth/login') {
        return _loginSuccess();
      }
      if (request.url.path == '/chat') {
        return http.Response(
          jsonEncode({
            'error': {
              'code': 'authentication_required',
              'detail': 'authentication is required',
            },
          }),
          401,
          headers: {'Content-Type': 'application/json'},
        );
      }
      return http.Response('{}', 404);
    });

    await _pumpApp(tester, client);
    await _signIn(tester);
    await tester.enterText(find.byType(TextField), 'How many students?');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.byIcon(Icons.send), findsNothing);
    expect(find.text('School ERP Assistant'), findsNothing);
  });

  testWidgets('logout revokes server session and clears local state', (tester) async {
    http.BaseRequest? logoutRequest;
    final client = MockClient((request) async {
      if (request.url.path == '/auth/login') {
        return _loginSuccess();
      }
      if (request.url.path == '/auth/logout') {
        logoutRequest = request;
        return http.Response(
          jsonEncode({'status': 'ok'}),
          200,
          headers: {'Content-Type': 'application/json'},
        );
      }
      return http.Response('{}', 404);
    });

    await _pumpApp(tester, client);
    await _signIn(tester);
    await tester.tap(find.byTooltip('Logout'));
    await tester.pumpAndSettle();

    expect(logoutRequest, isNotNull);
    expect(logoutRequest!.method, 'POST');
    expect(logoutRequest!.url.path, '/auth/logout');
    expect(_lowercaseHeaders(logoutRequest!)['authorization'], 'Bearer $_token');
    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byIcon(Icons.send), findsNothing);
  });

  testWidgets('logout error still clears local state', (tester) async {
    final client = MockClient((request) async {
      if (request.url.path == '/auth/login') {
        return _loginSuccess();
      }
      if (request.url.path == '/auth/logout') {
        return http.Response(
          jsonEncode({'detail': 'server_error'}),
          500,
          headers: {'Content-Type': 'application/json'},
        );
      }
      return http.Response('{}', 404);
    });

    await _pumpApp(tester, client);
    await _signIn(tester);
    await tester.tap(find.byTooltip('Logout'));
    await tester.pumpAndSettle();

    expect(find.text('Sign in'), findsOneWidget);
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.byIcon(Icons.send), findsNothing);
  });
}
