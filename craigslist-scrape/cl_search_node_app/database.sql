CREATE TABLE books (
  id SERIAL PRIMARY KEY,
  title VARCHAR(100) UNIQUE NOT NULL,
  author VARCHAR(50) NOT NULL,
  genre VARCHAR(100) NOT NULL,
  published DATE NOT NULL,
  edition INTEGER,
  publisher VARCHAR(120)
);

INSERT INTO books (title, author, genre, published, edition, publisher)
VALUES ('Harry Potter and the Chamber of Secrets', 'JK Rowling', 'fantasy', '6-26-1999', 1, 'Scholastic'),
('Harry Potter and the Prisoner of Azkaban', 'JK Rowling', 'fantasy', '6-26-2001', 1, 'Scholastic'),
('Harry Potter and the Goblet of Fire', 'JK Rowling', 'fantasy', '6-26-2003', 1, 'Scholastic'),
('Harry Potter and the Order of the Pheonix', 'JK Rowling', 'fantasy', '6-26-2005', 1, 'Scholastic'),
('Harry Potter and the Half-Blood Prince', 'JK Rowling', 'fantasy', '6-26-2005', 1, 'Scholastic'),
('Harry Potter and the Deathly Hallows', 'JK Rowling', 'fantasy', '6-26-2003', 1, 'Scholastic'),
('Harry Potter and the Cursed Child', 'JK Rowling', 'fantasy', '6-26-2003', 1, 'Scholastic');
